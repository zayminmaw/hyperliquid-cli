"""Trade resolution — the monitor close-out (PLAN.md §5, §7).

Each pass, an open trade is checked against the current mark: if price has crossed
its stop-loss or take-profit (or the trade has aged past `max_hold_minutes`), the
position is closed and the outcome — won/lost/expired, realized P&L, R-multiple —
is written to the `trades` ledger. That ledger is what the Phase 4 tuner reads, so
without this step there are no resolved outcomes to learn from.

On paper the close is a `reduce_only` limit order *at the trigger price*, so the
book realizes exactly the P&L we record. On a live backend (`native_protected`),
native exchange-side triggers (Phase 5) are the real protection — there the
resolver stays the ledger's source of truth and the close is a `reduce_only`
*market* flatten, harmless if a native trigger already closed the position.

Live reconciliation goes beyond the mark: a native trigger can fire on a wick and
the mark can recover before the next pass, leaving the exchange flat while the
ledger still says "open". So on a protected network a *vanished* position is also
resolved — outcome inferred from the mark, else from the candle extremes since
entry, else booked `closed` at the mark (an external/manual close). After any live
close the coin's surviving reduce-only triggers are cancelled, so half of an old
SL/TP pair can never ambush the next position.
"""

from __future__ import annotations

import httpx

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Order, OrderType, Side
from hlcli.exchange.base import Exchange
from hlcli.executor.protect import cancel_coin_triggers
from hlcli.state.store import StateStore


def resolve_open_trades(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    now: float,
    *,
    marks: dict[str, float] | None = None,
    native_protected: bool = False,
    shadow_only: bool = False,
) -> int:
    """Close every open trade whose SL/TP/expiry has triggered. Returns the count closed.

    Shadow rows (hypothetical trades logged by shadow mode) resolve *orderlessly* at
    their trigger level — that is how shadow builds tuner/graduation outcomes without
    a book. `shadow_only=True` (a shadow pass) leaves real trades untouched: a shadow
    pass may hold a read-only exchange and must never place a close order."""
    marks = marks if marks is not None else exchange.get_marks()
    live_coins = {p.coin for p in exchange.get_positions()} if native_protected and not shadow_only else None
    closed = 0

    for trade in state.open_trades():
        if trade["shadow"]:
            closed += _resolve_shadow(state, trade, marks, now, tunable)
            continue
        if shadow_only:
            continue  # a shadow pass never touches real trades
        mark = marks.get(trade["coin"])
        if mark is None:
            continue

        vanished = live_coins is not None and trade["coin"] not in live_coins
        outcome = _classify(trade, mark, now, tunable)
        if outcome is None and vanished:
            # The exchange is flat but the ledger says open — a native trigger (or a
            # manual close) beat the mark check. Book the outcome anyway.
            outcome = _classify_vanished(exchange, trade, mark)
        if outcome is None:
            continue  # still live

        status, level_price = outcome
        side = Side(trade["side"])
        exit_price = level_price
        if not vanished:
            result = exchange.place_order(_close_order(trade, side, level_price, native_protected))
            # On a live market close, book the *actual* fill — keeps the ledger (and the
            # graduation expectancy that gates mainnet) honest about slippage. A native
            # trigger that beat us to the close reports no fill, so we fall back to the
            # level; paper fills its LIMIT exactly at the level by construction.
            if native_protected and result.avg_price is not None:
                exit_price = result.avg_price
        realized, r_multiple = _pnl(trade, side, exit_price)
        state.resolve_trade(trade["id"], status, exit_price, realized, r_multiple, now)
        if native_protected:
            cancel_coin_triggers(exchange, trade["coin"])  # the surviving half of the SL/TP pair
        closed += 1

    return closed


def _resolve_shadow(state: StateStore, trade: dict, marks: dict[str, float], now: float,
                    tunable: TunableConfig) -> int:
    """Book a shadow trade's outcome at its trigger level — no order, no book."""
    mark = marks.get(trade["coin"])
    if mark is None:
        return 0
    outcome = _classify(trade, mark, now, tunable)
    if outcome is None:
        return 0
    status, level_price = outcome
    realized, r_multiple = _pnl(trade, Side(trade["side"]), level_price)
    state.resolve_trade(trade["id"], status, level_price, realized, r_multiple, now)
    return 1


def _close_order(trade: dict, side: Side, exit_price: float, native_protected: bool) -> Order:
    """Paper: a LIMIT at the level the book realizes exactly. Live: a reduce-only MARKET
    flatten — a no-op if a native trigger already closed the position."""
    if native_protected:
        return Order(
            coin=trade["coin"], side=_opposite(side), order_type=OrderType.MARKET,
            size=trade["size"], reduce_only=True,
        )
    return Order(
        coin=trade["coin"], side=_opposite(side), order_type=OrderType.LIMIT,
        size=trade["size"], price=exit_price, reduce_only=True,
    )


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


def _classify_vanished(exchange: Exchange, trade: dict, mark: float) -> tuple[str, float]:
    """Outcome for a position the exchange no longer holds, though the mark never told
    us why. The candle extremes since entry say which level a wick crossed; SL is
    checked first (a whipsaw that touched both books the loss — pessimistic, so the
    graduation expectancy is never flattered). If no level was touched, someone closed
    it externally → `closed` at the mark."""
    try:
        bars = [b for b in exchange.get_candles(trade["coin"]) if b.t >= trade["opened_at"] * 1000]
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        bars = []

    side = Side(trade["side"])
    lows, highs = [b.l for b in bars], [b.h for b in bars]
    if side is Side.LONG:
        if lows and min(lows) <= trade["sl"]:
            return "lost", trade["sl"]
        if highs and max(highs) >= trade["tp"]:
            return "won", trade["tp"]
    else:
        if highs and max(highs) >= trade["sl"]:
            return "lost", trade["sl"]
        if lows and min(lows) <= trade["tp"]:
            return "won", trade["tp"]
    return "closed", mark  # closed externally (manual flatten, liquidation, …)


def _pnl(trade: dict, side: Side, exit_price: float) -> tuple[float, float]:
    """Realized P&L and R-multiple (reward in units of the trade's initial risk)."""
    per_unit = (exit_price - trade["entry"]) if side is Side.LONG else (trade["entry"] - exit_price)
    risk = abs(trade["entry"] - trade["sl"])
    realized = round(per_unit * trade["size"], 6)
    r_multiple = round(per_unit / risk, 4) if risk > 0 else 0.0
    return realized, r_multiple


def _opposite(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG
