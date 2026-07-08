"""Sentry 6c — the management gate (PLAN.md §14).

Deterministic, first-failure-wins, same contract as the entry gate: the LLM's
management verdict is an *input*, never a bypass. The gate owns everything the
prompt merely asks for — the ratchet direction, the churn caps, the
breakeven-before-extend rule — so a bad verdict is rejected here, not trusted.

Risk can only go down or stay — with one earned exception. tighten/reduce/close
are the permitted risk direction; extend_tp is the "let it run" action (already
protected at breakeven, bounded to one initial-R per move, never inside the
opposing window of a recent bank); and ADD (6d) is the lone risk-increasing
action, admitted only under the full pyramid discipline: the position is a
winner at/above `sentry_add_min_r`, the stop rises in the same action, the add's
entire risk is covered by unrealized profit, each add is at most half the
current size, the entry-path notional/leverage caps are re-run on the enlarged
position, and a lifetime per-coin add budget applies. The model nominates; the
code sizes.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Side
from hlcli.executor.rmath import favorable_move, initial_risk
from hlcli.sentry.decision import ManagementAction, ManagementDecision
from hlcli.sentry.engine import MoveStop, ScaleOut

# The most the take-profit may move per approved extend_tp, in units of the
# trade's initial risk — "let it run" in bounded, auditable steps.
_MAX_TP_EXTENSION_R = 1.0


@dataclass(frozen=True)
class CloseAll:
    """Flatten the position at market; the ledger books the actual exit."""

    level: float  # the mark at gate time — paper closes exactly here


@dataclass(frozen=True)
class MoveTP:
    new_tp: float


@dataclass(frozen=True)
class AddTo:
    """Pyramid: buy/sell `size` more at market, with the stop raised to `new_stop`
    for the whole enlarged position. Size was computed by the gate, never the model."""

    size: float
    new_stop: float


Plan = MoveStop | ScaleOut | CloseAll | MoveTP | AddTo


@dataclass
class ManageGateContext:
    """Everything the gate needs, gathered by the live pass. The clock fields come
    from the sentry log — the log IS the churn state, so a restart can't reset it."""

    caps: Caps
    tunable: TunableConfig
    mark: float
    now: float
    breaker_tripped: bool
    daily_loss_hit: bool
    last_applied_ts: float | None  # most recent applied managed action on this trade
    actions_today: int             # applied managed actions on this trade, rolling 24h
    last_bank_ts: float | None     # most recent reduce/scale-out (the extend↔bank window)
    last_extend_ts: float | None   # most recent extend_tp (the bank↔extend window)
    equity: float = 0.0            # for the ADD leverage re-check
    coin_adds: int = 0             # lifetime managed_add count for this coin (ADD budget)
    coin_size: float = 0.0         # the coin's TOTAL open size (an add creates sibling rows)


@dataclass
class ManageOutcome:
    approved: bool
    reason: str = "ok"
    plan: Plan | None = None  # what apply should execute; None for hold/reject


def evaluate_management(decision: ManagementDecision, trade: dict, ctx: ManageGateContext) -> ManageOutcome:
    if decision.action is ManagementAction.HOLD:
        return ManageOutcome(approved=True, reason="hold")

    # A full CLOSE ends all risk, so it clears even when halted and is never blocked by
    # the churn caps — the caps stop flip-flopping, not the one action that ends flopping.
    if decision.action is ManagementAction.CLOSE:
        return ManageOutcome(True, plan=CloseAll(level=ctx.mark))

    halted = ctx.breaker_tripped or ctx.daily_loss_hit
    if halted and decision.action in (ManagementAction.EXTEND_TP, ManagementAction.ADD):
        return ManageOutcome(False, "halted: risk may only go down")
    if ctx.actions_today >= ctx.caps.sentry_max_actions_per_position_per_day:
        return ManageOutcome(False, "per-position action budget exhausted")
    if _within(ctx.last_applied_ts, ctx.caps.sentry_min_action_interval_minutes, ctx.now):
        return ManageOutcome(False, "cooldown: an action was applied recently")
    if decision.action is ManagementAction.EXTEND_TP and _within(
            ctx.last_bank_ts, ctx.caps.sentry_opposing_window_minutes, ctx.now):
        return ManageOutcome(False, "opposing window: banked profit recently, no extend")
    if decision.action is ManagementAction.REDUCE and _within(
            ctx.last_extend_ts, ctx.caps.sentry_opposing_window_minutes, ctx.now):
        return ManageOutcome(False, "opposing window: extended recently, no reduce")

    side = Side(trade["side"])
    risk = initial_risk(trade)
    if risk <= 0:
        return ManageOutcome(False, "no measurable initial risk")

    if decision.action is ManagementAction.TIGHTEN_STOP:
        return _check_tighten(decision.new_stop, trade, side, risk, ctx)
    if decision.action is ManagementAction.REDUCE:
        return _check_reduce(decision.reduce_pct, trade, ctx)
    if decision.action is ManagementAction.ADD:
        return _check_add(decision.new_stop, trade, side, risk, ctx)
    return _check_extend(decision.new_tp, trade, side, risk)


def _check_tighten(new_stop: float, trade: dict, side: Side, risk: float,
                   ctx: ManageGateContext) -> ManageOutcome:
    improvement = (new_stop - trade["sl"]) if side is Side.LONG else (trade["sl"] - new_stop)
    if improvement <= 0:
        return ManageOutcome(False, "stop would not tighten (ratchet)")
    if improvement < ctx.tunable.trail.min_move_r * risk:
        return ManageOutcome(False, "dust move (below min_move_r)")
    if (new_stop >= ctx.mark) if side is Side.LONG else (new_stop <= ctx.mark):
        return ManageOutcome(False, "stop at/past the mark would fire instantly")
    return ManageOutcome(True, plan=MoveStop(new_sl=new_stop, reason="llm"))


def _check_reduce(pct: float, trade: dict, ctx: ManageGateContext) -> ManageOutcome:
    # One partial per trade, whether the ladder or the LLM banked it — stacking
    # reductions is churn, and the shared `scaled_out` flag/idempotency key keeps
    # the crash-safety story identical to 6a. A full CLOSE remains available.
    if trade["scaled_out"]:
        return ManageOutcome(False, "already scaled once (close remains available)")
    close_size = trade["size"] * pct / 100.0
    risk = initial_risk(trade)
    r_now = round(favorable_move(trade, ctx.mark) / risk, 4) if risk > 0 else 0.0
    return ManageOutcome(True, plan=ScaleOut(size=close_size, level=ctx.mark, r=r_now))


def _check_add(new_stop: float, trade: dict, side: Side, risk: float,
               ctx: ManageGateContext) -> ManageOutcome:
    """The full pyramid discipline. The model nominated an add + a raised stop; the
    size comes out of these checks, never out of the verdict."""
    if trade["coin"] not in ctx.caps.coins:
        return ManageOutcome(False, "coin no longer allowed")
    if ctx.coin_adds >= ctx.caps.sentry_max_adds_per_position:
        return ManageOutcome(False, "per-position add budget exhausted")

    favorable = favorable_move(trade, ctx.mark)
    if favorable / risk < ctx.caps.sentry_add_min_r:
        return ManageOutcome(False, f"adds only to winners at ≥ {ctx.caps.sentry_add_min_r}R")

    raised = (new_stop - trade["sl"]) if side is Side.LONG else (trade["sl"] - new_stop)
    if raised <= 0:
        return ManageOutcome(False, "an add must raise the stop with it")
    if (new_stop >= ctx.mark) if side is Side.LONG else (new_stop <= ctx.mark):
        return ManageOutcome(False, "raised stop at/past the mark would fire instantly")

    # Safe-pyramid sizing: the add's whole risk (entered at the mark, stopped at the
    # raised stop) must be covered by THIS row's unrealized profit (attribution-clean
    # and conservative when a prior add created sibling rows), each add is at most
    # half the coin's current size, and the enlarged position re-clears the entry
    # path's notional + leverage caps against the coin's TOTAL size.
    add_risk_per_unit = abs(ctx.mark - new_stop)
    held = max(ctx.coin_size, trade["size"])
    unrealized = favorable * trade["size"]
    by_profit = unrealized / add_risk_per_unit
    by_half = 0.5 * held
    by_notional = ctx.caps.max_notional_per_trade / ctx.mark - held
    by_leverage = (ctx.equity * ctx.caps.max_leverage) / ctx.mark - held
    size = min(by_profit, by_half, by_notional, by_leverage)
    if size <= 0:
        return ManageOutcome(False, "no room to add inside the caps")
    return ManageOutcome(True, plan=AddTo(size=round(size, 10), new_stop=new_stop))


def _check_extend(new_tp: float, trade: dict, side: Side, risk: float) -> ManageOutcome:
    at_breakeven = (trade["sl"] >= trade["entry"]) if side is Side.LONG else (trade["sl"] <= trade["entry"])
    if not at_breakeven:
        return ManageOutcome(False, "extend requires the stop at breakeven or better")
    extension = (new_tp - trade["tp"]) if side is Side.LONG else (trade["tp"] - new_tp)
    if extension <= 0:
        return ManageOutcome(False, "target would not extend")
    if extension > _MAX_TP_EXTENSION_R * risk:
        return ManageOutcome(False, f"extension exceeds {_MAX_TP_EXTENSION_R}R per action")
    return ManageOutcome(True, plan=MoveTP(new_tp=new_tp))


def _within(ts: float | None, window_minutes: float, now: float) -> bool:
    return ts is not None and (now - ts) < window_minutes * 60.0
