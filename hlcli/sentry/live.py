"""Sentry 6c — the live management pass (PLAN.md §14).

The LLM manager acts, but only through the management gate, and only in the
risk-reducing direction (tighten/reduce/close; extend_tp is the bounded
exception; ADD does not exist until 6d). Every step is throttled by hard caps
that live in `.env`, off-limits to the model and the tuner:

  - a position is *evaluated* at most every `sentry_eval_interval_minutes`
    (bounds LLM spend and hold-log volume);
  - `sentry_max_llm_calls_per_day` is the global rolling-24h backstop;
  - applied actions respect a per-position cooldown, a per-position daily
    budget, and the extend↔bank opposing window — all read from the sentry
    log itself, so a restart cannot reset the churn clocks.

Real trades only: the hypothetical shadow book keeps its 6a rules + 6b
proposals, so it stays a clean record of *entry* decision quality.

Every evaluation is logged: `managed_hold`, `managed_rejected`,
`managed_dropped`, or the applied `managed_<action>` row with the verdict's
confidence and rationale — the audit trail and, later, tuner fuel.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Candle
from hlcli.exchange.base import Exchange
from hlcli.executor.regime import classify
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.sentry.apply import (
    ManageSummary,
    apply_close,
    apply_move_stop,
    apply_move_tp,
    apply_scale_out,
)
from hlcli.sentry.context import FAST_INTERVAL, SLOW_INTERVAL, build_context, frames_for, labeled
from hlcli.sentry.decision import decide_management
from hlcli.sentry.engine import MoveStop, ScaleOut
from hlcli.sentry.gate import CloseAll, ManageGateContext, MoveTP, evaluate_management
from hlcli.sentry.shadow import ManageFn
from hlcli.state.store import StateStore

# Log-row vocabularies — the sentry log is also the churn state (see gate docstring).
_APPLIED = ("managed_tighten_stop", "managed_reduce", "managed_close", "managed_extend_tp")
_EVALUATED = _APPLIED + ("managed_hold", "managed_rejected", "managed_dropped")
_BANK_SIDE = ("managed_reduce", "scale_out")  # rule ladder counts toward the opposing window too
_EXTEND_SIDE = ("managed_extend_tp",)

_DAY_SECONDS = 86_400.0


@dataclass
class LiveSummary:
    evaluated: int = 0
    held: int = 0
    applied: int = 0
    rejected: int = 0
    dropped: int = 0
    spaced: int = 0     # skipped: inside the per-position eval interval
    failed: int = 0     # gate-approved but died at the exchange
    note: str = "ok"
    actions: list[dict] = field(default_factory=list)


def manage_live(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    decide_fn: ManageFn = decide_management,
    marks: dict[str, float] | None = None,
    native_protected: bool = False,
    alerter: Alerter | None = None,
    now: float | None = None,
) -> LiveSummary:
    now = now if now is not None else time.time()
    marks = marks if marks is not None else exchange.get_marks()
    breaker = Breaker(state, caps)
    breaker_tripped = breaker.tripped()
    daily_loss = breaker.daily_loss_hit(exchange.equity())

    summary = LiveSummary()
    day_ago = now - _DAY_SECONDS
    calls_today = state.sentry_count_since(day_ago, _EVALUATED)
    bars_cache: dict[str, tuple[list[Candle], list[Candle]]] = {}
    applied = ManageSummary()  # the apply helpers tally into this; folded in below

    for trade in state.open_trades(shadow=False):
        mark = marks.get(trade["coin"])
        if mark is None:
            continue
        last_eval = state.last_sentry_ts(trade["id"], _EVALUATED)
        if last_eval is not None and (now - last_eval) < caps.sentry_eval_interval_minutes * 60:
            summary.spaced += 1
            continue
        if calls_today >= caps.sentry_max_llm_calls_per_day:
            summary.note = "daily LLM call budget exhausted"
            break

        fast, slow = frames_for(exchange, trade["coin"], bars_cache)
        ctx = build_context(
            trade, mark=mark, state=state, tunable=tunable, now=now,
            regime=classify(fast), candles=labeled(FAST_INTERVAL, fast),
            candles_slow=labeled(SLOW_INTERVAL, slow), breaker_tripped=breaker_tripped,
        )
        result = decide_fn(ctx, caps, tunable)
        calls_today += 1
        summary.evaluated += 1

        if result.dropped:
            summary.dropped += 1
            state.log_sentry(now, trade["id"], trade["coin"], "managed_dropped",
                             {"note": result.note, "stop_reason": result.stop_reason,
                              "raw": result.raw})
            continue

        decision = result.decision
        gate_ctx = ManageGateContext(
            caps=caps, tunable=tunable, mark=mark, now=now,
            breaker_tripped=breaker_tripped, daily_loss_hit=daily_loss,
            last_applied_ts=state.last_sentry_ts(trade["id"], _APPLIED),
            actions_today=state.sentry_count_since(day_ago, _APPLIED, trade["id"]),
            last_bank_ts=state.last_sentry_ts(trade["id"], _BANK_SIDE),
            last_extend_ts=state.last_sentry_ts(trade["id"], _EXTEND_SIDE),
        )
        outcome = evaluate_management(decision, trade, gate_ctx)

        if outcome.plan is None:
            if outcome.approved:  # hold
                summary.held += 1
                state.log_sentry(now, trade["id"], trade["coin"], "managed_hold",
                                 {"confidence": decision.confidence, "rationale": decision.rationale,
                                  "mark": mark})
            else:
                summary.rejected += 1
                state.log_sentry(now, trade["id"], trade["coin"], "managed_rejected",
                                 {"proposal": decision.as_dict(), "reason": outcome.reason})
                _emit(alerter, "sentry_rejected", coin=trade["coin"],
                      action=decision.action.value, reason=outcome.reason)
            continue

        _apply(exchange, state, trade, outcome.plan, decision, now,
               native_protected=native_protected, summary=applied, alerter=alerter)

    summary.applied = applied.stops_moved + applied.scaled_out + applied.closed + applied.tps_moved
    summary.failed = applied.failed
    summary.actions = applied.actions
    return summary


def _apply(exchange, state, trade, plan, decision, now, *, native_protected, summary, alerter) -> None:
    extra = {"confidence": decision.confidence, "rationale": decision.rationale}
    kw = dict(native_protected=native_protected, summary=summary, alerter=alerter, extra=extra)
    if isinstance(plan, MoveStop):
        apply_move_stop(exchange, state, trade, plan, now, log_action="managed_tighten_stop", **kw)
    elif isinstance(plan, ScaleOut):
        apply_scale_out(exchange, state, trade, plan, now, log_action="managed_reduce", **kw)
    elif isinstance(plan, CloseAll):
        apply_close(exchange, state, trade, plan.level, now, log_action="managed_close", **kw)
    elif isinstance(plan, MoveTP):
        apply_move_tp(exchange, state, trade, plan.new_tp, now, log_action="managed_extend_tp", **kw)


def _emit(alerter: Alerter | None, event: str, **fields) -> None:
    if alerter is not None:
        alerter.alert(event, level="warning", **fields)
