"""Trade resolution — the monitor close-out (PLAN.md §5, §7).

Each pass, an open trade is checked against the current mark: if price has crossed
its stop-loss or take-profit (or the trade has aged past `max_hold_minutes`), the
position is closed and the outcome — won/lost/expired, realized P&L, R-multiple —
is written to the `trades` ledger. That ledger is what the Phase 4 tuner reads, so
without this step there are no resolved outcomes to learn from.

The close is a `reduce_only` limit order *at the trigger price*, so the paper book
realizes exactly the P&L we record. This is the executor-side monitor; native
exchange-side trigger orders (which protect a crashed process) are a Phase 5
mainnet prerequisite, not built here.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Order, OrderType, Side
from hlcli.exchange.base import Exchange
from hlcli.state.store import StateStore


def resolve_open_trades(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    now: float,
    *,
    marks: dict[str, float] | None = None,
) -> int:
    """Close every open trade whose SL/TP/expiry has triggered. Returns the count closed."""
    marks = marks if marks is not None else exchange.get_marks()
    closed = 0

    for trade in state.open_trades():
        mark = marks.get(trade["coin"])
        if mark is None:
            continue

        outcome = _classify(trade, mark, now, tunable)
        if outcome is None:
            continue  # still live

        status, exit_price = outcome
        side = Side(trade["side"])
        exchange.place_order(Order(
            coin=trade["coin"], side=_opposite(side), order_type=OrderType.LIMIT,
            size=trade["size"], price=exit_price, reduce_only=True,
        ))
        realized, r_multiple = _pnl(trade, side, exit_price)
        state.resolve_trade(trade["id"], status, exit_price, realized, r_multiple, now)
        closed += 1

    return closed


def _classify(trade: dict, mark: float, now: float, tunable: TunableConfig) -> tuple[str, float] | None:
    """(status, exit_price) if the trade should close now, else None. SL/TP win over expiry."""
    side = Side(trade["side"])
    if side is Side.LONG:
        if mark <= trade["sl"]:
            return "lost", trade["sl"]
        if mark >= trade["tp"]:
            return "won", trade["tp"]
    else:
        if mark >= trade["sl"]:
            return "lost", trade["sl"]
        if mark <= trade["tp"]:
            return "won", trade["tp"]

    if tunable.max_hold_minutes and (now - trade["opened_at"]) / 60.0 > tunable.max_hold_minutes:
        return "expired", mark
    return None


def _pnl(trade: dict, side: Side, exit_price: float) -> tuple[float, float]:
    """Realized P&L and R-multiple (reward in units of the trade's initial risk)."""
    per_unit = (exit_price - trade["entry"]) if side is Side.LONG else (trade["entry"] - exit_price)
    risk = abs(trade["entry"] - trade["sl"])
    realized = round(per_unit * trade["size"], 6)
    r_multiple = round(per_unit / risk, 4) if risk > 0 else 0.0
    return realized, r_multiple


def _opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG
