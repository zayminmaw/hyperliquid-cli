"""Self-tuning: cohort gating (no cohort ⇒ model not called), the mocked config +
prompt tuners, clamps holding on proposals, and the promote/diff/history flow. The
tuner LLM is never hit — fake clients return canned payloads."""

import json
from types import SimpleNamespace

from hlcli.core.config_schema import TunableConfig, clamp, load_tunable
from hlcli.core.types import Side
from hlcli.state.store import StateStore
from hlcli.tuner.config_tuner import propose_config
from hlcli.tuner.promote import diff, history, paths, promote, write_proposed_config, write_proposed_prompt
from hlcli.tuner.prompt_tuner import propose_prompt
from hlcli.tuner.stats import cohorts, summary
from hlcli.tests._helpers import caps

NOW = 1_000_000.0


def _caps(tmp_path):
    return caps(config_path=tmp_path / "active_config.json")


def _seed(state, n, *, coin="BTC", side=Side.LONG, conv=0.8, won=True):
    """Insert n resolved trades in one cohort."""
    for i in range(n):
        tid = state.open_trade(f"c{i}", coin, side, 100, 90, 120, 1.0, conv, None, NOW)
        r, realized, status = (2.0, 20.0, "won") if won else (-1.0, -10.0, "lost")
        state.resolve_trade(tid, status, 120 if won else 90, realized, r, NOW)


class FakeTool:
    """Returns one forced tool call with the given input."""

    def __init__(self, name, payload):
        self._name, self._payload = name, payload
        self.messages = self

    def create(self, **kwargs):
        block = SimpleNamespace(type="tool_use", name=self._name, input=self._payload)
        return SimpleNamespace(content=[block])


class FakeText:
    def __init__(self, text):
        self._text = text
        self.messages = self

    def create(self, **kwargs):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self._text)])


class Boom:
    """A client that explodes if used — proves the sample gate skips the model."""

    def __init__(self):
        self.messages = self

    def create(self, **kwargs):
        raise AssertionError("model must not be called when the sample gate fails")


# --- stats / cohorts ---

def test_cohorts_are_sample_gated(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 4)  # below MIN_COHORT_SAMPLES (5)
    assert cohorts(state.resolved_trades()) == []


def test_cohort_stats(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 4, won=True)
    _seed(state, 1, won=False)  # 5 total in BTC/long/high → 4 wins
    [c] = cohorts(state.resolved_trades())
    assert c.key == "BTC/long/high" and c.n == 5 and c.wins == 4 and c.win_rate == 0.8
    assert summary(state.resolved_trades())["n"] == 5


# --- config tuner ---

_VALID_CFG = {
    "risk_per_trade_pct": 0.8,
    "regime": {"enabled": True, "allowed_regimes": ["trend"]},
    "sizing": {"min_conviction": 0.4, "floor_fraction": 0.3, "ceil_fraction": 0.9},
    "max_candidates_per_pass": 6,
    "decision_temperature": 0.3,
    "max_hold_minutes": 120,
}


def test_config_tuner_gated_without_cohort(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 3)
    res = propose_config(state, _caps(tmp_path), clamp(TunableConfig()), client=Boom())
    assert res.note == "no_eligible_cohort" and res.proposed is None


def test_config_tuner_proposes_clamped(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 6)
    res = propose_config(state, _caps(tmp_path), clamp(TunableConfig()), client=FakeTool("submit_config", _VALID_CFG))
    assert res.note == "ok" and res.proposed.risk_per_trade_pct == 0.8


def test_config_tuner_clamps_out_of_bounds_output(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 6)
    rogue = {**_VALID_CFG, "risk_per_trade_pct": 999,
             "sizing": {"min_conviction": 0.5, "floor_fraction": 0.9, "ceil_fraction": 0.2},
             "max_hold_minutes": 999_999}
    res = propose_config(state, _caps(tmp_path), clamp(TunableConfig()), client=FakeTool("submit_config", rogue))
    assert res.proposed.risk_per_trade_pct == 5.0           # clamped to ceiling
    assert res.proposed.max_hold_minutes == 10_080
    assert res.proposed.sizing.floor_fraction <= res.proposed.sizing.ceil_fraction


def test_config_tuner_drops_invalid_output(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 6)
    bad = {**_VALID_CFG, "risk_per_trade_pct": "not a number"}  # type error → ValidationError
    res = propose_config(state, _caps(tmp_path), clamp(TunableConfig()), client=FakeTool("submit_config", bad))
    assert res.note == "invalid_output" and res.proposed is None


# --- prompt tuner ---

def test_prompt_tuner_gated_without_data(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 3)
    assert propose_prompt(state, _caps(tmp_path), "PROMPT", client=Boom()).note == "insufficient_data"


def test_prompt_tuner_proposes(tmp_path):
    state = StateStore(tmp_path / "s.db")
    _seed(state, 5)
    res = propose_prompt(state, _caps(tmp_path), "PROMPT", client=FakeText("  refined prompt  "))
    assert res.note == "ok" and res.proposed == "refined prompt"


def test_prompt_tuner_strips_a_wrapping_code_fence(tmp_path):
    # `promote` writes the proposal verbatim — a fenced output must not ship its fences.
    state = StateStore(tmp_path / "s.db")
    _seed(state, 5)
    res = propose_prompt(state, _caps(tmp_path), "PROMPT",
                         client=FakeText("```markdown\nrefined prompt\n```"))
    assert res.proposed == "refined prompt"


def test_prompt_tuner_sees_rationales_and_readable_prompt(tmp_path):
    # The decision *reasoning* is the tuner's main signal; the current prompt goes in a
    # tag rather than being JSON-escaped into one long string.
    state = StateStore(tmp_path / "s.db")
    _seed(state, 5)
    state.log_decision("c0", NOW, decision={
        "candidate_id": "c0", "action": "act", "timing": "now",
        "conviction": 0.8, "rationale": "clean pullback to support",
    })

    class Capture(FakeText):
        def create(self, **kwargs):
            self.kwargs = kwargs
            return super().create(**kwargs)

    client = Capture("refined prompt")
    propose_prompt(state, _caps(tmp_path), "CURRENT PROMPT", client=client)
    content = client.kwargs["messages"][0]["content"]
    assert "<current_prompt>\nCURRENT PROMPT\n</current_prompt>" in content
    assert "clean pullback to support" in content


# --- promote / diff / history ---

def test_promote_makes_proposals_active_and_records(tmp_path):
    c = _caps(tmp_path)
    write_proposed_config(c, TunableConfig(risk_per_trade_pct=1.2))
    write_proposed_prompt(c, "new active prompt")

    promoted = promote(c, now=NOW)
    assert sorted(p["kind"] for p in promoted) == ["config", "prompt"]
    assert load_tunable(c.config_path).risk_per_trade_pct == 1.2
    assert paths(c).active_prompt.read_text() == "new active prompt"
    assert history(c)[0]["kind"] in ("config", "prompt") and len(history(c)) == 2


def test_promote_reclamps_hand_edited_proposal(tmp_path):
    c = _caps(tmp_path)
    p = paths(c)
    p.proposed_config.parent.mkdir(parents=True, exist_ok=True)
    p.proposed_config.write_text(json.dumps({"risk_per_trade_pct": 999}))  # raw, unclamped
    promote(c, kinds=("config",), now=NOW)
    assert load_tunable(c.config_path).risk_per_trade_pct == 5.0           # clamped on promote


def test_diff_reports_changed_fields(tmp_path):
    c = _caps(tmp_path)
    write_proposed_config(c, TunableConfig(risk_per_trade_pct=2.0))
    assert diff(c)["config"]["risk_per_trade_pct"]["proposed"] == 2.0


def test_promote_nothing_when_no_proposals(tmp_path):
    assert promote(_caps(tmp_path), now=NOW) == []


def test_promotion_consumes_the_proposal(tmp_path):
    # A promoted proposal is gone — a stale file can't be re-promoted weeks later
    # after newer `tune run`s produced nothing.
    c = _caps(tmp_path)
    write_proposed_config(c, TunableConfig(risk_per_trade_pct=1.2))
    write_proposed_prompt(c, "prompt v2")
    promote(c, now=NOW)
    p = paths(c)
    assert not p.proposed_config.exists() and not p.proposed_prompt.exists()
    assert promote(c, now=NOW + 1) == []  # second promote is a no-op


def test_promotion_audit_records_what_went_live(tmp_path):
    c = _caps(tmp_path)
    write_proposed_config(c, TunableConfig(risk_per_trade_pct=1.2))
    write_proposed_prompt(c, "prompt v2")
    promote(c, now=NOW)
    entries = {e["kind"]: e for e in history(c)}
    assert entries["config"]["config"]["risk_per_trade_pct"] == 1.2
    assert entries["prompt"]["chars"] == len("prompt v2") and "sha256" in entries["prompt"]


# --- L-4: the conviction-calibration table (the gate for re-enabling conviction sizing) ---

def test_conviction_calibration_buckets_and_exclusions():
    from hlcli.tuner.stats import conviction_calibration

    def t(conv, status, r):
        return {"conviction": conv, "status": status, "r_multiple": r, "realized": r * 10}

    rows = [
        t(0.9, "won", 2.0), t(0.8, "lost", -1.0),   # high bucket: n=2, win_rate .5, avg_r .5
        t(0.5, "lost", -1.0),                       # mid bucket
        t(0.2, "expired", 0.3),                     # low bucket (expired counts — a real outcome)
        # Excluded: scaled duplicates the parent's conviction; aborts are mechanical failures.
        t(0.9, "scaled", 1.0), t(0.9, "aborted", -0.02), t(0.9, "abort_failed", 0.0),
    ]
    cal = conviction_calibration(rows)
    assert [c["bucket"] for c in cal] == ["low", "mid", "high"]
    high = cal[-1]
    assert high["n"] == 2 and high["win_rate"] == 0.5 and high["avg_r"] == 0.5


def test_conviction_calibration_empty_book_is_empty():
    from hlcli.tuner.stats import conviction_calibration

    assert conviction_calibration([]) == []


def test_conviction_calibration_skips_adopted_and_missing_r():
    from hlcli.tuner.stats import conviction_calibration

    def t(conv, status, r, **kw):
        return {"conviction": conv, "status": status, "r_multiple": r,
                "realized": (r or 0.0) * 10, **kw}

    rows = [
        t(0.9, "won", 2.0),
        t(0.8, "closed", None),         # counted in n/win_rate, but never as a 0R in avg_r
        t(0.0, "won", 3.0, adopted=1),  # adopted: no LLM verdict behind it — excluded entirely
    ]
    cal = conviction_calibration(rows)
    assert [c["bucket"] for c in cal] == ["high"]  # the adopted row opened no low bucket
    assert cal[0]["n"] == 2 and cal[0]["avg_r"] == 2.0  # missing R didn't drag avg_r down

    only_missing = conviction_calibration([t(0.9, "closed", None)])
    assert only_missing[0]["avg_r"] is None  # no R evidence reads as none, not as flat


# --- execution-quality metrics (audit C/D) ---

def test_performance_empty_book():
    from hlcli.tuner.stats import performance

    perf = performance([], starting_equity=1_000.0)
    assert perf["n"] == 0 and perf["sharpe"] is None and perf["max_drawdown_pct"] == 0.0


def test_performance_over_equity_curve():
    from hlcli.tuner.stats import performance

    def t(realized, closed_at):
        return {"realized": realized, "closed_at": closed_at, "status": "won", "shadow": 0}

    # +20, -10, +20, -10 on a 1000 base → equity 1020,1010,1030,1020; PF = 40/20 = 2.0;
    # deepest dip is 1030→1020 vs the earlier 1020→1010: 10/1020 = 0.98% is the worst.
    rows = [t(20, 1), t(-10, 2), t(20, 3), t(-10, 4)]
    perf = performance(rows, starting_equity=1_000.0)
    assert perf["profit_factor"] == 2.0
    assert perf["max_drawdown_pct"] == 0.98
    assert perf["sharpe"] is not None and perf["sortino"] is not None


def test_performance_ratios_none_when_untrustworthy():
    from hlcli.tuner.stats import performance

    one = performance([{"realized": 5.0, "closed_at": 1, "status": "won"}], starting_equity=1_000.0)
    assert one["sharpe"] is None  # a single trade has no dispersion to divide by

    winners = [{"realized": r, "closed_at": i, "status": "won"} for i, r in enumerate([5.0, 7.0, 3.0])]
    perf = performance(winners, starting_equity=1_000.0)
    assert perf["profit_factor"] is None and perf["sortino"] is None  # no losers = no downside


def test_performance_avg_entry_slip_signed_and_excludes_shadow():
    from hlcli.tuner.stats import performance

    def t(side, entry, mark, shadow=0):
        return {"realized": 5.0, "closed_at": 1, "status": "won",
                "side": side, "entry": entry, "mark_at_entry": mark, "shadow": shadow}

    rows = [
        t("long", 101.0, 100.0),           # paid 1% above the mark
        t("short", 99.0, 100.0),           # sold 1% below the mark — also adverse
        t("long", 200.0, 100.0, shadow=1),  # shadow enters at the mark: excluded
    ]
    assert performance(rows, starting_equity=1_000.0)["avg_slip_pct"] == 1.0
