"""Paper exchange — the default backend (PLAN.md §3).

Simulated, keyless. Reads (marks/book) come from the **public mainnet** feed so
paper trades against real prices.

Two modes:
  - in-memory (no `state`): Phase-1 manual paper — no book, orders just recorded.
  - state-backed (`state` given): the executor's paper — fills are simulated and
    persisted to `paper_positions`, so the book and equity survive a restart and
    the gate's one-per-coin / max-concurrent checks stay correct.
"""

from __future__ import annotations

from itertools import count

from hlcli.core.types import Fill, Network, OpenOrder, Order, OrderResult, OrderType, Position, Side
from hlcli.exchange.marks import MarksFeed, api_url
from hlcli.state.store import StateStore


class PaperExchange:
    network = Network.PAPER

    def __init__(
        self,
        starting_equity: float,
        marks: MarksFeed | None = None,
        state: StateStore | None = None,
    ) -> None:
        self._starting_equity = starting_equity
        self._marks = marks or MarksFeed(api_url(Network.PAPER))
        self._state = state
        self._mem: list[Position] = []
        self._order_ids = count(1)

    def get_marks(self) -> dict[str, float]:
        return self._marks.all_marks()

    def get_book(self, coin: str) -> dict | None:
        return self._marks.book(coin)

    def get_candles(self, coin: str, *, interval: str = "15m", lookback: int = 48):
        return self._marks.candles(coin, interval=interval, lookback=lookback)

    def get_positions(self) -> list[Position]:
        if self._state is None:
            return list(self._mem)
        marks = self.get_marks()
        positions = []
        for coin, p in self._state.paper_positions().items():
            mark = marks.get(coin, p["entry_price"])
            pnl_unit = (mark - p["entry_price"]) if p["side"] is Side.LONG else (p["entry_price"] - mark)
            positions.append(Position(
                coin=coin, side=p["side"], size=p["size"], entry_price=p["entry_price"],
                unrealized_pnl=round(pnl_unit * p["size"], 6),
            ))
        return positions

    def equity(self) -> float:
        if self._state is None:
            return self._starting_equity
        unrealized = sum(p.unrealized_pnl for p in self.get_positions())
        return round(self._starting_equity + self._state.paper_realized() + unrealized, 6)

    def get_open_orders(self) -> list[OpenOrder]:
        return []  # paper fills immediately; no resting book in Phase 2

    def recent_fills(self, since_ms: int) -> list[Fill]:
        return []  # paper realizes at the resolver's level — no separate fill feed

    def place_order(self, order: Order) -> OrderResult:
        oid = f"paper-{next(self._order_ids)}"
        if self._state is None:
            return OrderResult(accepted=True, status="recorded", order_id=oid, message="paper book")

        if order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT):
            # A trigger has no resting book here — filling it instantly at mark would
            # fake a protective order that never protected anything. Paper protection
            # is the executor-side resolver; reject rather than lie.
            return OrderResult(
                accepted=False, status="error",
                message="paper book does not rest trigger orders; the resolver is paper's protection",
            )

        fill_price = order.price if order.order_type is OrderType.LIMIT else self.get_marks().get(order.coin)
        if fill_price is None:
            return OrderResult(accepted=False, status="error", message=f"no mark for {order.coin}")
        self._apply_fill(order, fill_price)
        return OrderResult(
            accepted=True, status="filled", order_id=oid, message=f"@ {fill_price}",
            filled_size=order.size, avg_price=fill_price,
        )

    def cancel(self, coin: str, oid: int) -> OrderResult:
        return OrderResult(accepted=True, status="canceled", message="paper book")

    def cancel_all(self, coin: str | None = None) -> int:
        return 0

    def set_leverage(self, coin: str, leverage: int, *, cross: bool = True) -> OrderResult:
        return OrderResult(accepted=True, status="leverage_set", message=f"{coin} {leverage}x (paper)")

    def _apply_fill(self, order: Order, price: float) -> None:
        existing = self._state.paper_positions().get(order.coin)

        if existing is None or existing["side"] is order.side:
            old_size = existing["size"] if existing else 0.0
            old_entry = existing["entry_price"] if existing else 0.0
            new_size = old_size + order.size
            new_entry = (old_entry * old_size + price * order.size) / new_size
            self._state.upsert_paper_position(order.coin, order.side, new_size, new_entry)
            return

        # opposite side → reduce / close, realizing P&L on the closed quantity
        entry, pos_side, pos_size = existing["entry_price"], existing["side"], existing["size"]
        closed = min(order.size, pos_size)
        pnl_unit = (price - entry) if pos_side is Side.LONG else (entry - price)
        self._state.add_paper_realized(pnl_unit * closed)
        remaining = pos_size - closed
        # A real book flips the excess into the new side — but reduce-only never opens.
        flipped = 0.0 if order.reduce_only else order.size - closed
        if remaining > 1e-12:
            self._state.upsert_paper_position(order.coin, pos_side, remaining, entry)
        elif flipped > 1e-12:
            self._state.upsert_paper_position(order.coin, order.side, flipped, price)
        else:
            self._state.delete_paper_position(order.coin)
