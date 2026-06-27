"""The deterministic risk gate (PLAN.md §5).

The LLM's decision is an **input** to the gate, never a bypass. Checks run as a
short-circuit pipeline, **first-failure wins**, in exactly this order:

    schema-valid decision → kill switch → daily-loss-limit → freshness
      → allowed-coin → regime sanity → level sanity → R:R floor
      → one-per-coin → max-concurrent → sizing + notional/leverage caps
      → conviction→size clamp

Everything that touches money lives here and in `_size` — sizing math, the caps,
the conviction clamp. Conviction only scales size *within* the hard caps; it can
never raise the ceiling. A rejected candidate returns the first failing reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from hlcli.core.config import Caps
from hlcli.core.config_schema import ConvictionSizing, TunableConfig
from hlcli.core.types import Action, Candidate, Decision, Order, OrderType, Side, Timing


@dataclass
class GateContext:
    """Everything the gate needs that isn't the candidate or the decision."""

    caps: Caps
    tunable: TunableConfig
    equity: float
    open_coins: set[str]
    open_count: int
    now: float
    breaker_tripped: bool = False
    daily_loss_hit: bool = False
    regime: str | None = None  # current regime signal (Phase 3 enrich); None = unknown, skip check


class GateOutcome(BaseModel):
    approved: bool
    reason: str | None = None  # first failing check, when rejected
    order: Order | None = None
    size: float = 0.0
    notional: float = 0.0


def evaluate(candidate: Candidate, decision: Decision, ctx: GateContext) -> GateOutcome:
    if decision.action is not Action.ACT:
        return _reject("decision: skip")
    if decision.timing is not Timing.NOW:
        return _reject("decision: wait")
    if ctx.breaker_tripped:
        return _reject("breaker tripped")
    if ctx.daily_loss_hit:
        return _reject("daily loss limit hit")

    age_minutes = (ctx.now - candidate.created_at) / 60.0
    if age_minutes > ctx.caps.max_signal_age_minutes:
        return _reject(f"stale: {age_minutes:.0f}m > {ctx.caps.max_signal_age_minutes}m")

    if candidate.coin not in ctx.caps.coins:
        return _reject(f"coin {candidate.coin} not in ALLOWED_COINS")

    if (
        ctx.tunable.regime.enabled
        and ctx.regime is not None
        and ctx.regime not in ctx.tunable.regime.allowed_regimes
    ):
        return _reject(f"regime {ctx.regime} not allowed")

    if not _levels_coherent(candidate):
        return _reject("incoherent levels (entry/sl/tp)")

    rr = _reward_risk(candidate)
    if rr < ctx.caps.rr_floor:
        return _reject(f"R:R {rr:.2f} < floor {ctx.caps.rr_floor}")

    if candidate.coin in ctx.open_coins:
        return _reject(f"already in a position for {candidate.coin}")

    if ctx.open_count >= ctx.caps.max_concurrent_positions:
        return _reject(f"max concurrent positions ({ctx.caps.max_concurrent_positions}) reached")

    size, notional = _size(candidate, decision, ctx)
    if size <= 0:
        return _reject("size clamped to zero (conviction below threshold)")

    order = Order(
        coin=candidate.coin,
        side=candidate.side,
        order_type=OrderType.LIMIT,
        size=size,
        price=candidate.entry,
        reduce_only=False,
    )
    return GateOutcome(approved=True, order=order, size=size, notional=notional)


def _size(candidate: Candidate, decision: Decision, ctx: GateContext) -> tuple[float, float]:
    """Fixed-fractional sizing, conviction-scaled, then clamped by the hard caps."""
    stop_distance = abs(candidate.entry - candidate.sl)
    if stop_distance <= 0:
        return 0.0, 0.0

    risk_amount = (ctx.tunable.risk_per_trade_pct / 100.0) * ctx.equity
    target_size = risk_amount / stop_distance
    target_size *= _conviction_fraction(decision.conviction, ctx.tunable.sizing)

    price = candidate.entry
    # Hard ceilings — conviction can never push size past these.
    max_by_notional = ctx.caps.max_notional_per_trade / price
    max_by_leverage = (ctx.equity * ctx.caps.max_leverage) / price

    size = round(min(target_size, max_by_notional, max_by_leverage), 6)
    return size, size * price


def _conviction_fraction(conviction: float, sizing: ConvictionSizing) -> float:
    """Map conviction → fraction of target size, within [floor, ceil]. Below min → 0."""
    if conviction < sizing.min_conviction:
        return 0.0
    span = (conviction - sizing.min_conviction) / max(1e-9, 1.0 - sizing.min_conviction)
    return sizing.floor_fraction + span * (sizing.ceil_fraction - sizing.floor_fraction)


def _levels_coherent(c: Candidate) -> bool:
    if c.side is Side.LONG:
        return c.sl < c.entry < c.tp
    return c.tp < c.entry < c.sl


def _reward_risk(c: Candidate) -> float:
    risk = abs(c.entry - c.sl)
    return abs(c.tp - c.entry) / risk if risk > 0 else 0.0


def _reject(reason: str) -> GateOutcome:
    return GateOutcome(approved=False, reason=reason)


def infer_side(entry: float, tp: float, sl: float) -> Side:
    """Side implied by level geometry. Raises if incoherent (caller surfaces it)."""
    if sl < entry < tp:
        return Side.LONG
    if tp < entry < sl:
        return Side.SHORT
    raise ValueError("levels incoherent: need sl<entry<tp (long) or tp<entry<sl (short)")
