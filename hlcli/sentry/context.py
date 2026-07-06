"""Sentry 6b — the management context (PLAN.md §14).

Assembles what the LLM manager judges one open position against: the position's
state in R, the original thesis (the human's reasoning + the entry-time verdict),
two candle timescales (position management fails when judged off a single frame),
the code-computed regime, and the position's own management history. Pure
assembly — no exchange calls, no LLM, and keyless by construction like
`EnrichedContext` (the audit log stores this verbatim).
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Side
from hlcli.state.store import StateStore


class ManagementContext(BaseModel):
    """The full input for one management verdict — JSON-serializable for prompt + log."""

    trade: dict            # id, coin, side, entry, size, sl, initial_sl, tp, r_now, age_minutes, scaled_out
    mark: float
    regime: str | None = None
    candles: dict | None = None       # fast frame: {"interval", "order", "bars"}
    candles_slow: dict | None = None  # slow frame — the longer-horizon view
    thesis: dict | None = None        # why this trade exists; None if the log has gaps
    prior_actions: list[dict]         # this trade's management history, newest first
    breaker_tripped: bool = False
    tunable: dict


def build_context(
    trade: dict,
    *,
    mark: float,
    state: StateStore,
    tunable: TunableConfig,
    now: float,
    regime: str | None = None,
    candles: dict | None = None,
    candles_slow: dict | None = None,
    breaker_tripped: bool = False,
) -> ManagementContext:
    initial_sl = trade["initial_sl"] or trade["sl"]
    risk = abs(trade["entry"] - initial_sl)
    favorable = (mark - trade["entry"]) if Side(trade["side"]) is Side.LONG else (trade["entry"] - mark)

    return ManagementContext(
        trade={
            "id": trade["id"],
            "coin": trade["coin"],
            "side": trade["side"],
            "entry": trade["entry"],
            "size": trade["size"],
            "sl": trade["sl"],
            "initial_sl": initial_sl,
            "tp": trade["tp"],
            "r_now": round(favorable / risk, 3) if risk > 0 else None,
            "age_minutes": round((now - trade["opened_at"]) / 60, 1),
            "scaled_out": bool(trade["scaled_out"]),
        },
        mark=mark,
        regime=regime,
        candles=candles,
        candles_slow=candles_slow,
        thesis=_thesis(state, trade["candidate_id"]),
        prior_actions=_prior_actions(state, trade["id"], now),
        breaker_tripped=breaker_tripped,
        # Only the trail surface — the manager should know what the rule baseline is
        # configured to do, not the hard caps and never a key.
        tunable={"trail": tunable.trail.model_dump()},
    )


def _thesis(state: StateStore, candidate_id: str) -> dict | None:
    """The reason this trade exists: the human's setup text + the entry verdict.
    Either half may be missing (pruned intake, pre-LLM stub decisions) — send what
    survives rather than nothing."""
    out: dict = {}
    candidate = state.intake_candidate(candidate_id)
    if candidate is not None:
        out["reasoning"] = candidate.reasoning
        out["news"] = candidate.news
        out["entry"] = candidate.entry  # the *planned* entry; the fill may differ
    row = state.decision_for(candidate_id)
    decision = _loads(row.get("decision")) if row else None
    if decision:
        out["entry_rationale"] = decision.get("rationale", "")
        out["entry_conviction"] = decision.get("conviction")
    return out or None


def _prior_actions(state: StateStore, trade_id: int, now: float) -> list[dict]:
    return [
        {
            "action": row["action"],
            "minutes_ago": round((now - row["ts"]) / 60, 1),
            "details": _loads(row["details"]),
        }
        for row in state.sentry_for_trade(trade_id)
    ]


def _loads(value):
    if not value:
        return None
    return json.loads(value) if isinstance(value, str) else value
