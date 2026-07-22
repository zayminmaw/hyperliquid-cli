"""LLM decision layer: validator/clamp (pure), the mocked decide call, and the
shadow / dropped paths through the executor pass. The real API is never hit — a
fake client returns canned payloads."""

import json
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


def test_prompt_deanchors_the_producer_verdict():
    # #3: the decision prompt must frame the producer's call as a second opinion, not a
    # verdict to ratify — the anchoring guard the value layer exists to enforce.
    from hlcli.executor.decision import SYSTEM_PROMPT
    assert "second opinion" in SYSTEM_PROMPT and "source_direction" in SYSTEM_PROMPT
    assert "anchoring trap" in SYSTEM_PROMPT  # and it names the failure mode both ways


def test_user_message_carries_the_producer_verdict():
    # The prompt claims the context "may also carry the producer's own call" — this locks
    # that the serialized context actually includes it, so a future _user_message refactor
    # that hand-picks fields can't silently drop the verdict and make the prompt lie.
    from hlcli.executor.decision import _user_message
    c = Candidate(id="x", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90,
                  source_direction="WAIT", source_confidence=0.4, created_at=NOW)
    ctx = enrich(c, marks={"BTC": 100.0}, equity=10_000.0, positions=[],
                 realized=0.0, recent=[], tunable=tunable())
    msg = _user_message(ctx)
    assert "source_direction" in msg and "WAIT" in msg


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
        # No tool block ≈ a truncated/refused generation, so mimic its stop_reason.
        stop = "end_turn" if self._payload is not None else "max_tokens"
        return SimpleNamespace(content=content, stop_reason=stop)


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
    assert res.stop_reason == "max_tokens"  # why it stopped rides along for the audit log


def test_decide_wraps_context_in_tags_with_compact_json():
    client = FakeClient(_good())
    decide(_ctx(), caps(), tunable(), client=client)
    content = client.kwargs["messages"][0]["content"]
    assert content.startswith("Judge the single candidate")
    inner = content.split("<context>\n", 1)[1].split("\n</context>", 1)[0]
    ctx = json.loads(inner)          # valid JSON…
    assert "\n" not in inner         # …and compact — indenting doubles the hot-loop cost
    assert ctx["candidate"]["coin"] == "BTC"


def test_decide_uses_order_path_model_and_low_temp():
    # Overridden to a model that accepts temperature — the default order-path model
    # (Sonnet 5) doesn't; that no-temperature path is covered separately below.
    client = FakeClient(_good())
    c = caps(decision_model="claude-sonnet-4-6")
    decide(_ctx(), c, tunable(), client=client)
    assert client.kwargs["model"] == c.decision_model
    assert client.kwargs["temperature"] == tunable().decision_temperature
    assert client.kwargs["tool_choice"]["name"] == "submit_decision"


@pytest.mark.parametrize("model", ["claude-opus-4-8", "claude-sonnet-5", "claude-fable-5"])
def test_decide_omits_temperature_for_no_sampling_models(model):
    # Opus 4.7+/Sonnet 5/Fable reject (non-default) sampling params with a 400, so the
    # order-path call must drop `temperature` when overridden to one of those families.
    client = FakeClient(_good())
    decide(_ctx(), caps(decision_model=model), tunable(), client=client)
    assert "temperature" not in client.kwargs


def test_recent_decision_rows_carry_coin_and_age():
    # Coin comes from the logged context; age from the row ts vs the pass `now`.
    row = {
        "candidate_id": "a", "ts": NOW - 600,
        "decision": json.dumps({"candidate_id": "a", "action": "act", "conviction": 0.7}),
        "fill": None, "context": json.dumps({"coin": "BTC"}),
    }
    c = Candidate(id="c1", coin="ETH", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW)
    ctx = enrich(c, marks={"ETH": 100.0}, equity=10_000.0, positions=[],
                 realized=0.0, recent=[row], tunable=tunable(), now=NOW)
    r = ctx.recent_decisions[0]
    assert (r["coin"], r["minutes_ago"], r["action"]) == ("BTC", 10.0, "act")


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
