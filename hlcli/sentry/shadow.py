"""Sentry 6b — the shadow pass (PLAN.md §14).

For every open trade: build the management context, ask the LLM manager for a
verdict, and log the proposal NEXT TO what the deterministic 6a baseline would do
at the same instant — then fire nothing. The paired log is the whole point: it is
the evidence that decides whether the LLM's judgment adds value over the rules
before it is ever allowed to act (6c), and it is gathered *before* the 6a engine
mutates the trade this pass.

Invalid output is dropped and tallied (`shadow_dropped`), never guessed at.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

import httpx

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Candle
from hlcli.exchange.base import Exchange
from hlcli.executor.regime import classify, summarize
from hlcli.sentry.context import build_context
from hlcli.sentry.decision import ManagementAction, ManagementResult, decide_management
from hlcli.sentry.engine import MoveStop, ScaleOut, plan
from hlcli.state.store import StateStore

ManageFn = Callable[..., ManagementResult]

_FAST_INTERVAL = "15m"  # matches the entry-decision tail
_SLOW_INTERVAL = "1h"   # the longer-horizon frame position management needs


@dataclass
class ShadowSummary:
    evaluated: int = 0
    held: int = 0       # proposals that said hold
    proposed: int = 0   # non-hold proposals
    dropped: int = 0
    agreed: int = 0     # proposal matches what the rule baseline would do
    actions: list[dict] = field(default_factory=list)


def shadow_pass(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    decide_fn: ManageFn = decide_management,
    marks: dict[str, float] | None = None,
    breaker_tripped: bool = False,
    now: float | None = None,
) -> ShadowSummary:
    """Propose-and-log over every open trade (real and hypothetical — the shadow
    book needs the same judgment or its outcomes stop being comparable)."""
    now = now if now is not None else time.time()
    marks = marks if marks is not None else exchange.get_marks()
    summary = ShadowSummary()
    bars_cache: dict[str, tuple[list[Candle], list[Candle]]] = {}

    for trade in state.open_trades():
        mark = marks.get(trade["coin"])
        if mark is None:
            continue
        fast, slow = _bars(exchange, trade["coin"], bars_cache)
        baseline = [_serialize(a) for a in plan(trade, mark, fast, tunable.trail)]
        ctx = build_context(
            trade, mark=mark, state=state, tunable=tunable, now=now,
            regime=classify(fast), candles=_labeled(_FAST_INTERVAL, fast),
            candles_slow=_labeled(_SLOW_INTERVAL, slow), breaker_tripped=breaker_tripped,
        )
        result = decide_fn(ctx, caps, tunable)
        summary.evaluated += 1

        if result.dropped:
            summary.dropped += 1
            state.log_sentry(now, trade["id"], trade["coin"], "shadow_dropped",
                             {"note": result.note, "stop_reason": result.stop_reason,
                              "raw": result.raw})
            continue

        decision = result.decision
        agrees = _agrees(decision.action, baseline)
        summary.held += int(decision.action is ManagementAction.HOLD)
        summary.proposed += int(decision.action is not ManagementAction.HOLD)
        summary.agreed += int(agrees)
        detail = {"proposal": decision.as_dict(), "baseline": baseline,
                  "agrees": agrees, "mark": mark, "r_now": ctx.trade["r_now"]}
        state.log_sentry(now, trade["id"], trade["coin"], "shadow", detail)
        summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"], **detail})

    return summary


def _agrees(action: ManagementAction, baseline: list[dict]) -> bool:
    """Crude proposal↔rule alignment for the value-add report: hold matches an idle
    baseline; a tighten matches a rule stop-move; reduce/close match a rule
    scale-out. extend_tp has no rule analog — judgment beyond the baseline."""
    kinds = {b["action"] for b in baseline}
    if action is ManagementAction.HOLD:
        return not kinds
    if action is ManagementAction.TIGHTEN_STOP:
        return "move_stop" in kinds
    if action in (ManagementAction.REDUCE, ManagementAction.CLOSE):
        return "scale_out" in kinds
    return False


def _serialize(action: ScaleOut | MoveStop) -> dict:
    if isinstance(action, ScaleOut):
        return {"action": "scale_out", "size": action.size, "level": action.level, "r": action.r}
    return {"action": "move_stop", "to": action.new_sl, "reason": action.reason}


def _labeled(interval: str, bars: list[Candle]) -> dict | None:
    tail = summarize(bars)
    return {"interval": interval, "order": "oldest_first", "bars": tail} if tail else None


def _bars(exchange: Exchange, coin: str, cache: dict) -> tuple[list[Candle], list[Candle]]:
    """Both timescales, once per coin per pass. Best-effort — a feed hiccup means a
    thinner context for this coin, never an aborted pass."""
    if coin not in cache:
        cache[coin] = (_fetch(exchange, coin, _FAST_INTERVAL), _fetch(exchange, coin, _SLOW_INTERVAL))
    return cache[coin]


def _fetch(exchange: Exchange, coin: str, interval: str) -> list[Candle]:
    try:
        return exchange.get_candles(coin, interval=interval)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return []
