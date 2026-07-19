"""R-math on a ledger `trade` dict — the one place the initial-risk anchor lives.

The whole R-anchoring decision (PLAN.md §14) hangs on measuring reward against the
trade's *initial* risk `|entry − initial_sl|`, not the working stop: once sentry
ratchets `sl` toward profit, |entry − sl| shrinks and would inflate every R the
tuner and graduation learn from. Every module that needs risk or the current
R-multiple imports these, so the anchor can't drift between call sites.
"""

from __future__ import annotations

from hlcli.core.types import Side


def initial_stop(trade: dict) -> float:
    """The stop the trade's R is measured against. `initial_sl` is set at entry and
    never moved; the `or sl` guards only rows written before the column existed."""
    return trade["initial_sl"] or trade["sl"]


def initial_risk(trade: dict) -> float:
    """|entry − initial_sl| — the trade's risk at entry, the unit of R."""
    return abs(trade["entry"] - initial_stop(trade))


def favorable_move(trade: dict, mark: float) -> float:
    """Signed price move in the trade's favour (positive = in profit)."""
    entry = trade["entry"]
    return (mark - entry) if Side(trade["side"]) is Side.LONG else (entry - mark)


def r_now(trade: dict, mark: float) -> float | None:
    """Unrealized reward in R at `mark`, or None when risk is non-positive."""
    risk = initial_risk(trade)
    if risk <= 0:
        return None
    return favorable_move(trade, mark) / risk


def taker_fee(rate_pct: float, size: float, entry: float, exit_price: float) -> float:
    """Round-trip taker fee for closing `size` at `exit_price` (wave-2 K): the rate is
    charged on both the entry notional and the exit notional. Composes across partial
    closes — summing per-close `size × (entry + exit)` recovers the full both-legs fee,
    since the entry-fee component sums to `rate × entry × total_size`. `rate_pct` is
    percent (Hyperliquid taker ≈ 0.045 — verify your live tier); 0 disables (the
    paper-parity switch the test caps use to keep gross assertions)."""
    return rate_pct / 100.0 * size * (entry + exit_price)
