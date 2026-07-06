"""Sentry 6b: the management validator/clamp, the mocked management call, the
context builder, the shadow pass (propose + log vs baseline, fire NOTHING), and
the sentry watch pass (deferred re-entry without touching intake)."""

import json
from types import SimpleNamespace

import pytest

from hlcli.core.config_schema import TrailConfig, TunableConfig, clamp
from hlcli.core.types import Candidate, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.runner import run_once
from hlcli.sentry.context import build_context
from hlcli.sentry.decision import (
    ManagementAction,
    ManagementResult,
    decide_management,
    validate_management,
)
from hlcli.sentry.shadow import shadow_pass
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps, tunable

NOW = 1_000_000.0


# --- validate_management: parse, clamp, drop ---------------------------------------


def _good(**over):
    return {"action": "hold", "confidence": 0.7, "rationale": "thesis intact",
            "new_stop": 0, "reduce_pct": 0, "new_tp": 0, **over}


def test_hold_parses():
    d = validate_management(_good(), 1)
    assert d.action is ManagementAction.HOLD and d.trade_id == 1
    assert d.new_stop is None and d.reduce_pct is None and d.new_tp is None


def test_action_params_are_extracted():
    assert validate_management(_good(action="tighten_stop", new_stop=101.5), 1).new_stop == 101.5
    assert validate_management(_good(action="reduce", reduce_pct=50), 1).reduce_pct == 50.0
    assert validate_management(_good(action="extend_tp", new_tp=140.0), 1).new_tp == 140.0


@pytest.mark.parametrize("raw,clamped", [(1.5, 1.0), (-0.2, 0.0), (0.4, 0.4)])
def test_confidence_clamped_not_dropped(raw, clamped):
    assert validate_management(_good(confidence=raw), 1).confidence == clamped


@pytest.mark.parametrize("payload", [
    None,
    "not a dict",
    {"confidence": 0.5},                              # missing action
    _good(action="add"),                              # ADD without a raised stop (new_stop=0)
    _good(confidence="high"),
    _good(confidence=float("nan")),
    _good(action="tighten_stop", new_stop=0),         # tighten with no usable stop
    _good(action="tighten_stop", new_stop=float("nan")),
    _good(action="tighten_stop", new_stop=-5),
    _good(action="reduce", reduce_pct=0),             # reduce with no fraction
    _good(action="reduce", reduce_pct=40),            # off the 25/50/75 ladder
    _good(action="extend_tp", new_tp="far"),
])
def test_invalid_payload_is_dropped(payload):
    assert validate_management(payload, 1) is None  # dropped, never guessed


def test_rationale_truncated():
    d = validate_management(_good(rationale="x" * 2000), 1)
    assert len(d.rationale) == 800


# --- decide_management (mocked client) ---------------------------------------------


class FakeClient:
    """Stands in for anthropic.Anthropic — records kwargs, returns a canned tool call."""

    def __init__(self, payload):
        self._payload = payload
        self.kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = SimpleNamespace(type="tool_use", name="submit_management", input=self._payload)
        content = [block] if self._payload is not None else []
        stop = "end_turn" if self._payload is not None else "max_tokens"
        return SimpleNamespace(content=content, stop_reason=stop)


def _trade_row(state, *, coin="BTC", shadow=False, size=1.0):
    tid = state.open_trade("c1", coin, Side.LONG, 100.0, 90.0, 130.0, size, 0.8, None, NOW,
                           shadow=shadow)
    return [t for t in state.open_trades() if t["id"] == tid][0]


def _ctx(tmp_path):
    state = StateStore(tmp_path / "ctx.db")
    trade = _trade_row(state)
    return build_context(trade, mark=110.0, state=state, tunable=tunable(), now=NOW + 600)


def test_decide_management_ok(tmp_path):
    client = FakeClient(_good(action="close"))
    res = decide_management(_ctx(tmp_path), caps(), tunable(), client=client)
    assert not res.dropped and res.decision.action is ManagementAction.CLOSE
    assert client.kwargs["tool_choice"] == {"type": "tool", "name": "submit_management"}
    assert client.kwargs["temperature"] == tunable().decision_temperature


def test_decide_management_drops_bad_output(tmp_path):
    res = decide_management(_ctx(tmp_path), caps(), tunable(), client=FakeClient({"action": "panic"}))
    assert res.dropped and res.note == "schema_invalid"
    res = decide_management(_ctx(tmp_path), caps(), tunable(), client=FakeClient(None))
    assert res.dropped and res.note == "no_decision" and res.stop_reason == "max_tokens"


def test_decide_management_omits_temperature_for_fable(tmp_path):
    client = FakeClient(_good())
    decide_management(_ctx(tmp_path), caps(decision_model="claude-fable-5"), tunable(), client=client)
    assert "temperature" not in client.kwargs


# --- context builder ----------------------------------------------------------------


def test_context_carries_thesis_and_history(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.enqueue(Candidate(id="c1", coin="BTC", side=Side.LONG, entry=100, tp=130, sl=90,
                            reasoning="breakout retest", news="ETF flows", created_at=NOW))
    state.log_decision("c1", NOW, decision={"candidate_id": "c1", "action": "act",
                                            "conviction": 0.8, "rationale": "clean levels"})
    trade = _trade_row(state)
    state.log_sentry(NOW + 60, trade["id"], "BTC", "move_stop", {"from": 90.0, "to": 100.5})
    state.log_sentry(NOW + 90, trade["id"], "BTC", "shadow", {"proposal": {"action": "close"}})

    ctx = build_context(trade, mark=110.0, state=state, tunable=tunable(), now=NOW + 120)
    assert ctx.thesis["reasoning"] == "breakout retest"
    assert ctx.thesis["entry_rationale"] == "clean levels" and ctx.thesis["entry_conviction"] == 0.8
    assert ctx.trade["r_now"] == 1.0 and ctx.trade["age_minutes"] == 2.0
    # Shadow proposals are NOT history — only what actually happened is shown.
    assert [a["action"] for a in ctx.prior_actions] == ["move_stop"]
    assert set(ctx.tunable) == {"trail"}  # never hard caps, never keys


def test_context_survives_missing_thesis(tmp_path):
    state = StateStore(tmp_path / "s.db")
    trade = _trade_row(state)  # no intake row, no decision log
    ctx = build_context(trade, mark=110.0, state=state, tunable=tunable(), now=NOW)
    assert ctx.thesis is None and ctx.prior_actions == []


# --- shadow pass --------------------------------------------------------------------


def _mgmt(payload):
    """An injectable decide_fn returning a canned validated result."""
    def fn(ctx, caps_, tunable_):
        d = validate_management(payload, ctx.trade["id"])
        if d is None:
            return ManagementResult(None, payload if isinstance(payload, dict) else None, "schema_invalid")
        return ManagementResult(d, payload, "ok")
    return fn


def _paper(tmp_path, marks):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    return state, ex


def _trail_on():
    return clamp(TunableConfig(trail=TrailConfig(breakeven_trigger_r=1.0))).trail


def test_shadow_logs_proposal_next_to_baseline(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    trade = _trade_row(state)
    cfg = tunable().model_copy(update={"trail": _trail_on()})

    s = shadow_pass(ex, state, caps(), cfg, decide_fn=_mgmt(_good(action="tighten_stop", new_stop=101.0)), now=NOW)
    assert s.evaluated == 1 and s.proposed == 1 and s.agreed == 1  # baseline also moves the stop
    (row,) = state.recent_sentry()
    assert row["action"] == "shadow"
    detail = json.loads(row["details"])
    assert detail["proposal"]["action"] == "tighten_stop"
    assert detail["baseline"][0]["action"] == "move_stop" and detail["agrees"] is True
    # Shadow NEVER acts: ledger and book untouched.
    assert state.open_trades()[0]["sl"] == 90.0 and ex.get_positions()[0].size == 1.0


def test_shadow_hold_agrees_with_idle_baseline(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 105.0})  # +0.5R: no rule triggers
    _trade_row(state)
    cfg = tunable().model_copy(update={"trail": _trail_on()})
    s = shadow_pass(ex, state, caps(), cfg, decide_fn=_mgmt(_good()), now=NOW)
    assert s.held == 1 and s.agreed == 1 and s.proposed == 0


def test_shadow_drop_is_tallied_and_logged(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    _trade_row(state)
    s = shadow_pass(ex, state, caps(), tunable(), decide_fn=_mgmt({"action": "panic"}), now=NOW)
    assert s.dropped == 1 and s.evaluated == 1
    (row,) = state.recent_sentry()
    assert row["action"] == "shadow_dropped"


def test_shadow_covers_hypothetical_book(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    _trade_row(state, shadow=True)
    s = shadow_pass(ex, state, caps(), tunable(), decide_fn=_mgmt(_good()), now=NOW)
    assert s.evaluated == 1  # the shadow book gets the same judgment


# --- sentry watch pass: deferred re-entry without intake ----------------------------


def test_watch_pass_recheks_deferred_but_never_consumes_intake(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 100.0, "ETH": 1500.0})
    # A parked WAIT candidate now due…
    parked = Candidate(id="w1", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90,
                       created_at=NOW - 60)
    state.defer_candidate(parked, next_check_at=NOW, attempts_remaining=2)
    # …and a fresh intake candidate sentry must NOT touch.
    state.enqueue(Candidate(id="n1", coin="ETH", side=Side.LONG, entry=1500, tp=1800,
                            sl=1400, created_at=NOW))

    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW, include_intake=False)
    assert s.rechecked == 1 and s.fired == 1      # the deferred setup entered via the gate
    assert s.seen == 0                            # intake untouched…
    assert state.get_hwm() == 0                   # …HWM not advanced
    assert len(state.pull_new()) == 1             # candidate still there for `exec`
    assert state.deferred_count() == 0            # parked entry consumed
    assert {t["coin"] for t in state.open_trades()} == {"BTC"}


def test_watch_pass_manages_and_resolves_too(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    _trade_row(state)
    cfg = tunable().model_copy(update={"trail": _trail_on()})
    s = run_once(ex, state, caps(), cfg, decide_fn=act_now, now=NOW + 60, include_intake=False)
    assert s.managed == 1 and state.open_trades()[0]["sl"] == 100.5
