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
