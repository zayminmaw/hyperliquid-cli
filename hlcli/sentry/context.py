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

import httpx
from pydantic import BaseModel

from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Candle
from hlcli.exchange.base import Exchange
from hlcli.executor.regime import DECISION_INTERVAL, summarize
from hlcli.executor.rmath import initial_stop, r_now
from hlcli.state.store import StateStore

FAST_INTERVAL = DECISION_INTERVAL  # matches the entry-decision tail
SLOW_INTERVAL = "1h"               # the longer-horizon frame position management needs


class ManagementContext(BaseModel):
    """The full input for one management verdict — JSON-serializable for prompt + log."""

    trade: dict            # id, coin, side, entry, size, sl, initial_sl, tp, r_now, age_minutes, scaled_out
    mark: float
    regime: str | None = None
    candles: dict | None = None       # fast frame: {"interval", "order", "bars"}
    candles_slow: dict | None = None  # slow frame — the longer-horizon view
    thesis: dict | None = None        # why this trade exists; None if the log has gaps
    prior_actions: list[dict]         # this trade's management history, newest first
    # Distilled lessons from our own recent journaled days (PLAN.md §15.4) —
    # advisory context, bounded by hard caps; the management gate still decides.
    recent_lessons: list[dict] | None = None
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
    lessons: list[dict] | None = None,
    breaker_tripped: bool = False,
) -> ManagementContext:
    initial_sl = initial_stop(trade)
    r = r_now(trade, mark)

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
            "r_now": round(r, 3) if r is not None else None,
            "age_minutes": round((now - trade["opened_at"]) / 60, 1),
            "scaled_out": bool(trade["scaled_out"]),
        },
        mark=mark,
        regime=regime,
        candles=candles,
        candles_slow=candles_slow,
        thesis=_thesis(state, trade["candidate_id"]),
        prior_actions=_prior_actions(state, trade["id"], now),
        recent_lessons=lessons or None,
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


# Rows that carry no material history for the manager: shadow proposals (never
# applied — a hypothetical reads like history to the model) and holds (a wall of
# them would evict the applied tightens/banks the manager actually needs to see).
_PRIOR_ACTION_NOISE = ("shadow", "shadow_dropped", "managed_hold")


def _prior_actions(state: StateStore, trade_id: int, now: float) -> list[dict]:
    """The applied management actions on this trade, newest first — the manager's own
    track record, with holds and shadow proposals filtered out so they can't crowd the
    window (observed live: a shadow tighten was described as 'a prior stop move')."""
    return [
        {
            "action": row["action"],
            "minutes_ago": round((now - row["ts"]) / 60, 1),
            "details": _loads(row["details"]),
        }
        for row in state.sentry_for_trade(trade_id, exclude=_PRIOR_ACTION_NOISE)
    ]


def _loads(value):
    if not value:
        return None
    return json.loads(value) if isinstance(value, str) else value


def labeled(interval: str, bars: list[Candle]) -> dict | None:
    """The prompt-facing candle block — bare bars are meaningless without a timeframe."""
    tail = summarize(bars)
    return {"interval": interval, "order": "oldest_first", "bars": tail} if tail else None


def frames_for(exchange: Exchange, coin: str, cache: dict) -> tuple[list[Candle], list[Candle]]:
    """Both timescales, once per coin per pass. Best-effort — a feed hiccup means a
    thinner context for this coin, never an aborted pass."""
    if coin not in cache:
        cache[coin] = (_fetch(exchange, coin, FAST_INTERVAL), _fetch(exchange, coin, SLOW_INTERVAL))
    return cache[coin]


def _fetch(exchange: Exchange, coin: str, interval: str) -> list[Candle]:
    try:
        return exchange.get_candles(coin, interval=interval)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return []
