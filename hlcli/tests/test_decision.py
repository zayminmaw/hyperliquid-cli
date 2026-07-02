"""LLM decision layer: validator/clamp (pure), the mocked decide call, and the
shadow / dropped paths through the executor pass. The real API is never hit — a
fake client returns canned payloads."""

from types import SimpleNamespace

import pytest

from hlcli.core.types import Action, Candidate, Side, Timing
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.decision import decide, validate_decision
from hlcli.executor.enrich import enrich
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps, drop, tunable

NOW = 1_000_000.0


# --- validate_decision: parse, clamp, drop ---

def _good(**over):
    return {"action": "act", "timing": "now", "conviction": 0.7, "rationale": "ok", **over}


def test_valid_payload_parses():
    d = validate_decision(_good(), "c1")
    assert (d.action, d.timing, d.conviction, d.candidate_id) == (Action.ACT, Timing.NOW, 0.7, "c1")


@pytest.mark.parametrize("raw,clamped", [(1.5, 1.0), (-0.2, 0.0), (0.4, 0.4)])
def test_conviction_is_clamped_not_dropped(raw, clamped):
    assert validate_decision(_good(conviction=raw), "c1").conviction == clamped


@pytest.mark.parametrize("payload", [
    None,
    "not a dict",
    {"timing": "now", "conviction": 0.5},          # missing action
    {"action": "act", "conviction": 0.5},           # missing timing
    {"action": "act", "timing": "now"},             # missing conviction
    _good(action="maybe"),                          # action outside enum
    _good(timing="soon"),                           # timing outside enum
    _good(conviction="high"),                       # non-numeric conviction
    _good(conviction=float("nan")),                 # NaN would clamp to 1.0 — max size
    _good(conviction="NaN"),                        # float("NaN") parses; still dropped
    _good(conviction=float("inf")),                 # non-finite → garbage, not a verdict
    _good(conviction=float("-inf")),
])
def test_invalid_payload_is_dropped(payload):
    assert validate_decision(payload, "c1") is None  # dropped, never guessed


def test_recheck_is_parsed_and_clamped():
    assert validate_decision(_good(timing="wait", recheck_in_minutes=5000), "c1").recheck_in_minutes == 1440.0
    assert validate_decision(_good(recheck_in_minutes=-3), "c1").recheck_in_minutes == 0.0
    assert validate_decision(_good(), "c1").recheck_in_minutes is None  # missing → code default later
    assert validate_decision(_good(recheck_in_minutes=float("nan")), "c1").recheck_in_minutes is None


# --- decide: mocked client ---

class FakeClient:
    """Stands in for anthropic.Anthropic — records kwargs, returns a canned tool call."""

    def __init__(self, payload):
        self._payload = payload
        self.kwargs = None
        self.messages = self

    def create(self, **kwargs):
        self.kwargs = kwargs
        block = SimpleNamespace(type="tool_use", name="submit_decision", input=self._payload)
        content = [block] if self._payload is not None else []
        return SimpleNamespace(content=content)


def _ctx():
    c = Candidate(id="c1", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW)
    return enrich(c, marks={"BTC": 100.0}, equity=10_000.0, positions=[],
                  realized=0.0, recent=[], tunable=tunable())


def test_decide_returns_validated_decision():
    res = decide(_ctx(), caps(), tunable(), client=FakeClient(_good(conviction=0.6)))
    assert not res.dropped and res.decision.conviction == 0.6 and res.note == "ok"


def test_decide_drops_malformed_output():
    res = decide(_ctx(), caps(), tunable(), client=FakeClient(_good(action="bogus")))
    assert res.dropped and res.note == "schema_invalid"


def test_decide_drops_when_model_skips_the_tool():
    res = decide(_ctx(), caps(), tunable(), client=FakeClient(None))
    assert res.dropped and res.note == "no_decision"


def test_decide_uses_order_path_model_and_low_temp():
    client = FakeClient(_good())
    decide(_ctx(), caps(), tunable(), client=client)
    assert client.kwargs["model"] == caps().decision_model           # claude-sonnet-4-6
    assert client.kwargs["temperature"] == tunable().decision_temperature
    assert client.kwargs["tool_choice"]["name"] == "submit_decision"


def test_decide_omits_temperature_for_no_sampling_models():
    # Opus 4.7+/Fable reject sampling params with a 400, so the order-path call must
    # drop `temperature` when the model is overridden to one of those families.
    client = FakeClient(_good())
    decide(_ctx(), caps(decision_model="claude-opus-4-8"), tunable(), client=client)
    assert "temperature" not in client.kwargs


# --- runner: shadow + dropped paths ---

def _setup(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
    return ex, state


def _cand(id):
    return Candidate(id=id, coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW)


def test_shadow_logs_but_fires_nothing(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), fire_enabled=False, decide_fn=act_now, now=NOW)
    assert (s.approved, s.fired) == (1, 0)
    assert ex.get_positions() == []          # nothing fired
    assert state.get_hwm() == 1              # but the candidate was consumed + logged
    assert state.recent_decisions(1)[0]["candidate_id"] == "a"


def test_dropped_decision_is_tallied_and_consumed(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=drop, now=NOW)
    assert (s.dropped, s.fired, s.approved) == (1, 0, 0)
    assert ex.get_positions() == []
    assert state.get_hwm() == 1              # dropped, but still advanced (not re-decided)
