"""The decision layer (PLAN.md §6).

The LLM owns *judgment* (act/skip, now/wait, conviction); deterministic code owns
everything that touches money. This module is the seam between them: it asks
`claude-sonnet-4-6` for a structured verdict on one candidate, then validates and
clamps that verdict before it ever reaches the gate. Output that fails schema
validation is **dropped and tallied, never guessed at** — we never default a
missing action to "skip" or invent a conviction.

`anthropic` is imported lazily inside `_make_client` so paper mode and the test
suite run with no key or SDK present; tests inject a fake `client`.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.llm import make_client
from hlcli.core.types import Action, Decision, Timing
from hlcli.executor.enrich import EnrichedContext

# Field order is deliberate: `rationale` first so the model states its read of the
# setup *before* committing to a verdict (with a forced tool call there is no
# separate thinking step, so the schema order is the reasoning order). JSON Schema
# strict mode can't bound `conviction` to [0, 1] (numeric constraints aren't
# supported by strict tool use), which is precisely why the code clamps it in
# `validate_decision`.
DECISION_TOOL = {
    "name": "submit_decision",
    "description": "Submit your execution judgment for the single candidate setup in the context.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string", "description": "One short sentence: your read of the setup at the current mark, the reasoning behind the verdict below."},
            "conviction": {"type": "number", "description": "Genuine edge in the setup as a decimal, 0.0 (none) to 1.0 (high)."},
            "timing": {"type": "string", "enum": ["now", "wait"], "description": "Enter now, or wait for a better moment."},
            "action": {"type": "string", "enum": ["act", "skip"], "description": "Take this setup, or pass."},
        },
        "required": ["rationale", "conviction", "timing", "action"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "You are the execution-judgment layer of a disciplined crypto-futures trading system. "
    "A human supplies the thesis (a candidate setup with entry/stop/target and reasoning); you "
    "supply execution judgment on ONE candidate at a time, given the current mark, a short tail of "
    "recent price candles, the code-inferred market regime, the portfolio, recent outcomes, and the "
    "active strategy config.\n\n"
    "Think like a seasoned execution trader, not a forecaster. Your edge is responding correctly, "
    "not predicting the market — disciplined behavior matters more than any single call, and chasing, "
    "forcing marginal trades, or sizing up to win back a recent loss is how accounts die. Let the "
    "setup come to you: WAIT for a clean entry or SKIP a marginal one rather than taking a mediocre "
    "fill now. Judge risk before reward — what invalidates this setup, and is the entry still good at "
    "the current mark? Stand aside when the picture is unclear: incoherent or contradictory levels, a "
    "regime that doesn't support the trade, or a mark that has already run past the entry. Capital "
    "preservation and consistency beat being right once.\n\n"
    "Decide only: action (act/skip), timing (now/wait), conviction, and a one-sentence rationale. "
    "Conviction is a decimal in [0,1] reflecting genuine edge, not enthusiasm: it scales position "
    "size within fixed risk caps, so ~0.5 is a setup you'd take at half size and 0.8+ is reserved for "
    "high-edge setups. You do NOT size positions, place stops, pick coins, or override any limit — "
    "deterministic code owns all sizing math and safety, and your verdict is validated and clamped "
    "before anything reaches the exchange.\n\n"
    "Be selective, and always answer by calling the submit_decision tool."
)


@dataclass
class DecisionResult:
    """A decision plus its provenance. `decision is None` means the output was dropped."""

    decision: Decision | None
    raw: dict | None  # what the model returned, for the audit log
    note: str  # "ok" | "schema_invalid" | "no_decision"

    @property
    def dropped(self) -> bool:
        return self.decision is None


def validate_decision(payload: object, candidate_id: str) -> Decision | None:
    """Parse + clamp a raw model payload into a `Decision`, or `None` to drop it.

    Out-of-range conviction is *clamped* (a safety bound, not a guess). A missing
    or non-numeric conviction, or an action/timing outside the enum, is *dropped*
    — we never fabricate a verdict the model didn't give.
    """
    if not isinstance(payload, dict):
        return None
    try:
        action = Action(payload["action"])
        timing = Timing(payload["timing"])
        conviction = float(payload["conviction"])
    except (KeyError, ValueError, TypeError):
        return None

    return Decision(
        candidate_id=candidate_id,
        action=action,
        timing=timing,
        conviction=max(0.0, min(1.0, conviction)),
        rationale=str(payload.get("rationale", ""))[:500],
    )


def decide(
    ctx: EnrichedContext,
    caps: Caps,
    tunable: TunableConfig,
    *,
    client=None,
) -> DecisionResult:
    """Ask the order-path model for a verdict on `ctx.candidate`, validated + clamped.

    Raises on an API/transport failure — the caller decides whether to abort the
    pass; a missing or malformed *output* is returned as a dropped result, never
    raised and never guessed.
    """
    client = client or make_client()
    kwargs = dict(
        model=caps.decision_model,
        max_tokens=caps.decision_max_tokens,
        system=load_decision_prompt(caps),
        tools=[DECISION_TOOL],
        tool_choice={"type": "tool", "name": "submit_decision"},
        messages=[{"role": "user", "content": ctx.model_dump_json(indent=2)}],
    )
    # `temperature` is rejected (400) by the Opus 4.7+ and Fable/Mythos families; the
    # order-path model is env-overridable, so only send it to a model that accepts it.
    if _supports_temperature(caps.decision_model):
        kwargs["temperature"] = tunable.decision_temperature
    response = client.messages.create(**kwargs)

    payload = _tool_payload(response)
    decision = validate_decision(payload, ctx.candidate.id)
    if decision is None:
        return DecisionResult(None, payload if isinstance(payload, dict) else None,
                              "schema_invalid" if payload is not None else "no_decision")
    return DecisionResult(decision, payload, "ok")


def load_decision_prompt(caps: Caps) -> str:
    """The active decision prompt (promoted by the prompt tuner), or the built-in default."""
    path = caps.config_path.with_name("active_prompt.md")
    return path.read_text() if path.exists() else SYSTEM_PROMPT


# Model families that reject sampling params (temperature/top_p/top_k) with a 400.
_NO_SAMPLING_PARAMS = ("claude-opus-4-7", "claude-opus-4-8", "claude-fable", "claude-mythos")


def _supports_temperature(model: str) -> bool:
    return not model.startswith(_NO_SAMPLING_PARAMS)


def _tool_payload(response) -> dict | None:
    """The `submit_decision` tool input from a response, or None if the model didn't call it."""
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_decision":
            return block.input
    return None
