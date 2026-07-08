"""Sentry 6b — the LLM management decision (PLAN.md §14).

Same seam as the entry decision layer: the model supplies *judgment* on one open
position (is the thesis intact? tighten, bank, close, or leave it alone?) from a
bounded action menu; deterministic code owns every number that reaches the
exchange. Output that fails validation is dropped and tallied, never guessed at.

Runs in shadow (6b — proposals logged next to the 6a rule baseline) and live
(6c/6d — through the management gate). ADD is the one risk-increasing action
(6d): the model may only *nominate* it with a raised stop; the gate enforces
every pyramid rule and the code computes the size.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.llm import make_client, supports_temperature
from hlcli.sentry.context import ManagementContext


class ManagementAction(StrEnum):
    HOLD = "hold"
    TIGHTEN_STOP = "tighten_stop"
    REDUCE = "reduce"
    CLOSE = "close"
    EXTEND_TP = "extend_tp"
    ADD = "add"  # 6d — the one risk-increasing action; the code sizes it, never the model


_REDUCE_STEPS = (25.0, 50.0, 75.0)

# Rationale first for the same reason as the entry tool: under a forced tool call
# the schema order is the model's only reasoning order. Strict mode can't bound
# numbers, so every numeric field is validated/clamped in code; the unused param
# convention ("0 when not applicable") keeps the schema strict-compatible without
# optional fields.
MANAGEMENT_TOOL = {
    "name": "submit_management",
    "description": "Submit your management verdict for the single open position in the context.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "rationale": {"type": "string", "description": "2-4 sentences, reasoned BEFORE the verdict: is the original thesis still intact at the current mark, has the regime or structure shifted since entry, and why the action below follows. Required for any action other than hold."},
            "confidence": {"type": "number", "description": "How clearly the evidence supports this action, 0.0 to 1.0."},
            "action": {"type": "string", "enum": ["hold", "tighten_stop", "reduce", "close", "extend_tp", "add"], "description": "hold = leave the position exactly as it is (the default); tighten_stop = move the stop toward profit; reduce = bank part of the position; close = exit fully at market; extend_tp = move the take-profit further out; add = pyramid into a clear winner (rare — the code sizes the add and enforces every pyramid rule)."},
            "new_stop": {"type": "number", "description": "For tighten_stop: the new stop price — it must protect MORE than the current stop (the code rejects anything wider). For add: the RAISED stop that will protect the whole enlarged position — an add is rejected unless the stop comes up with it. 0 for other actions."},
            "reduce_pct": {"type": "number", "enum": [0, 25, 50, 75], "description": "For reduce: the percentage of the position to close. 0 for other actions."},
            "new_tp": {"type": "number", "description": "For extend_tp: the new take-profit price, further from entry than the current one. 0 for other actions."},
        },
        "required": ["rationale", "confidence", "action", "new_stop", "reduce_pct", "new_tp"],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "You are the in-trade management judgment of a disciplined crypto-futures trading system. "
    "You are shown ONE open position at a time: its entry thesis (the human's reasoning and the "
    "entry-time verdict), its current state (entry, stop, target, unrealized R, age), the current "
    "mark, two candle timescales, the code-inferred regime, and the position's own management "
    "history. A `recent_lessons` block, when present, holds lessons distilled from your own recent "
    "trading days — advisory context, never an override of the position in front of you. "
    "Deterministic trailing/breakeven/scale-out rules already run on this book; you add "
    "judgment those rules cannot: recognizing that the thesis is broken, that the structure that "
    "justified the trade has changed, or that profit should be protected ahead of schedule.\n\n"
    "HOLD is the default and the most common correct answer. Winning trade management is "
    "pre-committed and boring: the stop and target were placed for a reason, and reacting to every "
    "candle is how edges die. Overtrading and micro-managing destroyed every undisciplined system "
    "that came before you — act only when the evidence in front of you clearly warrants it, and "
    "say hold otherwise.\n\n"
    "Your menu: hold (no change); tighten_stop (protect more — e.g. the move has run far beyond "
    "entry, or the thesis is weakening but not dead); reduce (bank 25/50/75% — e.g. into strength "
    "at a level, or when conviction has genuinely faded); close (the thesis is invalidated — news "
    "reversed, structure broke, the reason for the trade is gone; do not wait for the stop when "
    "you know); extend_tp (only when the position is already protected at breakeven or better and "
    "the move is clearly trending beyond the original target). You can never widen a stop, never "
    "add size, and never move a target closer to force a win — risk only ever goes down or stays. "
    "Every number you give is validated and clamped by deterministic code before anything could "
    "reach an exchange.\n\n"
    "The one exception to risk-only-down is add: pyramiding into a position that is clearly "
    "working — trending decisively in your favor with the thesis strengthening, not merely green. "
    "Adds are rare and earn their place: the code only permits one when the position is at least "
    "+1R, the stop is raised in the same action (give a new_stop that protects the whole enlarged "
    "position), and the add's entire risk is covered by unrealized profit. You never choose the "
    "add's size — the code computes it and caps it. When in doubt, hold or tighten instead.\n\n"
    "State your rationale first — thesis intact or broken, what changed since entry — then the "
    "verdict, and always answer by calling the submit_management tool."
)


@dataclass(frozen=True)
class ManagementDecision:
    trade_id: int
    action: ManagementAction
    confidence: float
    rationale: str
    new_stop: float | None = None   # tighten_stop only
    reduce_pct: float | None = None  # reduce only
    new_tp: float | None = None     # extend_tp only

    def as_dict(self) -> dict:
        return {
            "trade_id": self.trade_id, "action": self.action.value,
            "confidence": self.confidence, "rationale": self.rationale,
            "new_stop": self.new_stop, "reduce_pct": self.reduce_pct, "new_tp": self.new_tp,
        }


@dataclass
class ManagementResult:
    """A management verdict plus provenance; `decision is None` means dropped."""

    decision: ManagementDecision | None
    raw: dict | None
    note: str  # "ok" | "schema_invalid" | "no_decision"
    stop_reason: str | None = None

    @property
    def dropped(self) -> bool:
        return self.decision is None


def validate_management(payload: object, trade_id: int) -> ManagementDecision | None:
    """Parse + clamp a raw payload into a `ManagementDecision`, or None to drop it.

    Structural validation only — whether a tighten actually tightens is the
    management gate's job (6c). But an action whose *own* parameter is missing or
    non-finite carries no usable verdict, so it is dropped, never defaulted.
    """
    if not isinstance(payload, dict):
        return None
    try:
        action = ManagementAction(payload["action"])
        confidence = float(payload["confidence"])
    except (KeyError, ValueError, TypeError):
        return None
    if not math.isfinite(confidence):
        return None

    new_stop = reduce_pct = new_tp = None
    if action in (ManagementAction.TIGHTEN_STOP, ManagementAction.ADD):
        new_stop = _positive_price(payload.get("new_stop"))  # an add must raise the stop with it
        if new_stop is None:
            return None
    elif action is ManagementAction.REDUCE:
        reduce_pct = _reduce_step(payload.get("reduce_pct"))
        if reduce_pct is None:
            return None
    elif action is ManagementAction.EXTEND_TP:
        new_tp = _positive_price(payload.get("new_tp"))
        if new_tp is None:
            return None

    return ManagementDecision(
        trade_id=trade_id,
        action=action,
        confidence=max(0.0, min(1.0, confidence)),
        rationale=str(payload.get("rationale", ""))[:800],
        new_stop=new_stop,
        reduce_pct=reduce_pct,
        new_tp=new_tp,
    )


def _positive_price(value: object) -> float | None:
    try:
        price = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return price if math.isfinite(price) and price > 0 else None


def _reduce_step(value: object) -> float | None:
    try:
        pct = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return pct if pct in _REDUCE_STEPS else None


def decide_management(
    ctx: ManagementContext,
    caps: Caps,
    tunable: TunableConfig,
    *,
    client=None,
) -> ManagementResult:
    """Ask the order-path model for a management verdict on one open position.

    Raises on an API/transport failure (caller's call, like `decide`); malformed
    *output* comes back as a dropped result.
    """
    client = client or make_client()
    kwargs = dict(
        model=caps.decision_model,
        max_tokens=caps.decision_max_tokens,
        system=SYSTEM_PROMPT,
        tools=[MANAGEMENT_TOOL],
        tool_choice={"type": "tool", "name": "submit_management"},
        messages=[{"role": "user", "content": _user_message(ctx)}],
    )
    if supports_temperature(caps.decision_model):
        kwargs["temperature"] = tunable.decision_temperature
    response = client.messages.create(**kwargs)

    stop_reason = getattr(response, "stop_reason", None)
    payload = _tool_payload(response)
    decision = validate_management(payload, ctx.trade["id"])
    if decision is None:
        return ManagementResult(None, payload if isinstance(payload, dict) else None,
                                "schema_invalid" if payload is not None else "no_decision",
                                stop_reason=stop_reason)
    return ManagementResult(decision, payload, "ok", stop_reason=stop_reason)


def _user_message(ctx: ManagementContext) -> str:
    return (
        "Judge the single open position in the context below per your instructions.\n"
        f"<context>\n{ctx.model_dump_json()}\n</context>"
    )


def _tool_payload(response) -> dict | None:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_management":
            return block.input
    return None
