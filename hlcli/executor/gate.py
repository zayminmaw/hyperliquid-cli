"""The deterministic risk gate (PLAN.md §5).

The LLM's decision is an **input** to the gate, never a bypass. Checks run as a
short-circuit pipeline, **first-failure wins**, in exactly this order:

    schema-valid decision → kill switch → daily-loss-limit → daily-entry-count → freshness
      → allowed-coin → regime sanity → level sanity → R:R floor
      → mark sanity (mark present, inside sl/tp, R:R at mark still clears)
      → one-per-coin → max-concurrent → sizing + notional/leverage caps
      → conviction→size clamp → account-wide gross exposure/leverage

The mark-sanity block is what keeps a stale thesis from becoming a bad MARKET
fill: the entry is a market order, so the *mark* — not the proposed entry — is
what we'll actually pay. If the mark has run past the entry far enough that the
reward:risk measured from the mark no longer clears the floor (or has crossed a
level outright), the code rejects it — the LLM's timing judgment is advisory,
this check is the guarantee. Sizing prices risk and the caps at the mark too.

Everything that touches money lives here and in `_size` — sizing math, the caps,
the conviction clamp. Conviction only scales size *within* the hard caps; it can
never raise the ceiling. A rejected candidate returns the first failing reason.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from hlcli.core.config import Caps
from hlcli.core.config_schema import ConvictionSizing, TunableConfig
from hlcli.core.types import Action, Candidate, Decision, Order, OrderType, Position, Side, Timing


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
    trades_today: int = 0  # new entries already opened this UTC day (grows as this pass fires)
    regime: str | None = None  # current regime signal (Phase 3 enrich); None = unknown, skip check
    mark: float | None = None  # current mark; None = no price → reject (a MARKET entry can't fire blind)
    gross_notional: float = 0.0  # open-book notional priced at the mark; the account-wide caps add this order to it
    maintenance_margin: float = 0.0  # cross maintenance margin required now (USDC); the pre-fire margin-health gate (M)


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
    if ctx.caps.max_trades_per_day > 0 and ctx.trades_today >= ctx.caps.max_trades_per_day:
        return _reject(f"daily entry limit reached ({ctx.caps.max_trades_per_day}/day)")

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

    if ctx.mark is None:
        return _reject(f"no mark for {candidate.coin}")
    if not _mark_inside_levels(candidate, ctx.mark):
        return _reject(f"mark {ctx.mark} outside sl/tp — setup invalidated or played out")
    rr_at_mark = _reward_risk_at(candidate, ctx.mark)
    if rr_at_mark < ctx.caps.rr_floor:
        return _reject(f"R:R at mark {rr_at_mark:.2f} < floor {ctx.caps.rr_floor} (entry has run)")

    if candidate.coin in ctx.open_coins:
        return _reject(f"already in a position for {candidate.coin}")

    if ctx.open_count >= ctx.caps.max_concurrent_positions:
        return _reject(f"max concurrent positions ({ctx.caps.max_concurrent_positions}) reached")

    if ctx.equity <= 0:
        return _reject("equity non-positive")

    # Maintenance-margin buffer (wave-2 M): refuse a new fire when the book is already close
    # to liquidation. `crossMaintenanceMarginUsed` is the liquidation threshold, so a high
    # fraction of equity means little cushion left to add risk. A hard cap, live only (paper
    # reports 0). 0 disables.
    if ctx.caps.max_maintenance_margin_frac > 0 and \
            ctx.maintenance_margin > ctx.equity * ctx.caps.max_maintenance_margin_frac:
        return _reject(
            f"maintenance margin {ctx.maintenance_margin / ctx.equity:.0%} of equity "
            f"> cap {ctx.caps.max_maintenance_margin_frac:.0%}")

    size, notional = _size(candidate, decision, ctx)
    if size <= 0:
        return _reject("size clamped to zero (conviction below threshold)")
    # Exchange floor (Hyperliquid: $10/order) — reject with a clear reason here rather
    # than a late exchange error. Mode B only; Mode A market orders bypass the gate and
    # get the exchange's own (clearer) reject.
    if notional < ctx.caps.min_order_notional:
        return _reject(
            f"notional {notional:.2f} below exchange minimum ${ctx.caps.min_order_notional:g}"
        )

    # Account-wide exposure ceiling (audit A). One-per-coin (checked above) means this order
    # opens a coin we don't already hold, so post-trade gross is current gross + this notional
    # with no netting. A hard cap: conviction and the LLM can never move it, and unlike the
    # per-order caps it bounds the *sum* across the whole book.
    exposure_reason = gross_exposure_reason(ctx.caps, ctx.gross_notional, notional, ctx.equity)
    if exposure_reason is not None:
        return _reject(exposure_reason)

    # A MARKET entry so an accepted order is a *filled* one — a resting GTC limit
    # would leave the ledger and protective triggers tracking a position that may
    # never open. The candidate's entry/sl/tp still drive sizing and protection.
    order = Order(
        coin=candidate.coin,
        side=candidate.side,
        order_type=OrderType.MARKET,
        size=size,
        reduce_only=False,
    )
    return GateOutcome(approved=True, order=order, size=size, notional=notional)


def _size(candidate: Candidate, decision: Decision, ctx: GateContext) -> tuple[float, float]:
    """Fixed-fractional sizing, conviction-scaled, then clamped by the hard caps.

    Priced at the *mark*, not the proposed entry: the entry order is a MARKET order,
    so the mark is what the fill (and therefore the true stop distance and notional)
    will actually be. The leverage ceiling here is per-order; the *account-wide* gross
    exposure/leverage bound (`max_total_exposure_usd` / `max_gross_leverage`) is a
    separate check in `evaluate`, applied to the sum across the whole book.
    """
    price = ctx.mark if ctx.mark is not None else candidate.entry
    stop_distance = abs(price - candidate.sl)
    if stop_distance <= 0:
        return 0.0, 0.0

    risk_amount = (ctx.tunable.risk_per_trade_pct / 100.0) * ctx.equity
    target_size = risk_amount / stop_distance
    target_size *= _conviction_fraction(decision.conviction, ctx.tunable.sizing)

    # Hard ceilings — conviction can never push size past these.
    max_by_notional = ctx.caps.max_notional_per_trade / price
    max_by_leverage = (ctx.equity * ctx.caps.max_leverage) / price

    size = round(min(target_size, max_by_notional, max_by_leverage), 6)
    return size, size * price


def _conviction_fraction(conviction: float, sizing: ConvictionSizing) -> float:
    """Map conviction → fraction of target size, within [floor, ceil]. Below min → 0.

    With scaling disabled (the default — conviction is uncalibrated until this book's
    outcomes prove otherwise) the fraction is 1.0: pure fixed-fractional sizing, where
    `risk_per_trade_pct` alone sets the risk and conviction is a logged signal only."""
    if not sizing.enabled:
        return 1.0
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


def _mark_inside_levels(c: Candidate, mark: float) -> bool:
    """Strictly between stop and target — beyond either, the setup is dead."""
    if c.side is Side.LONG:
        return c.sl < mark < c.tp
    return c.tp < mark < c.sl


def _reward_risk_at(c: Candidate, mark: float) -> float:
    """R:R measured from the mark — what a MARKET fill here would actually get."""
    risk = abs(mark - c.sl)
    return abs(c.tp - mark) / risk if risk > 0 else 0.0


def _reject(reason: str) -> GateOutcome:
    return GateOutcome(approved=False, reason=reason)


def book_gross_notional(positions: list[Position], marks: dict[str, float]) -> float:
    """Total open-book notional, mark-priced with an entry-price fallback (fail-closed — a
    dropped quote must not undercount the book and loosen the account-wide cap)."""
    return sum(abs(p.size) * (marks.get(p.coin) or p.entry_price) for p in positions)


def gross_exposure_reason(caps: Caps, gross_notional: float, notional: float, equity: float) -> str | None:
    """First failing account-wide exposure cap for adding `notional` to a book already carrying
    `gross_notional`, or None if within caps. Shared by the Mode B gate and Mode A `trade` so
    both honor the same hard ceilings. Each cap is off when 0; leverage is skipped on
    non-positive equity (the dollar cap still applies)."""
    post_gross = gross_notional + notional
    if caps.max_total_exposure_usd > 0 and post_gross > caps.max_total_exposure_usd:
        return f"gross exposure {post_gross:.0f} > cap {caps.max_total_exposure_usd:g}"
    if caps.max_gross_leverage > 0 and equity > 0 and post_gross > equity * caps.max_gross_leverage:
        return f"gross leverage {post_gross / equity:.2f}x > cap {caps.max_gross_leverage:g}x"
    return None


def infer_side(entry: float, tp: float, sl: float) -> Side:
    """Side implied by level geometry. Raises if incoherent (caller surfaces it)."""
    if sl < entry < tp:
        return Side.LONG
    if tp < entry < sl:
        return Side.SHORT
    raise ValueError("levels incoherent: need sl<entry<tp (long) or tp<entry<sl (short)")
