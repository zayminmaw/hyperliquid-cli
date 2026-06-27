"""Paper exchange — the default backend (PLAN.md §3).

Simulated, in-memory, keyless. Reads (marks/book) come from the **public mainnet**
feed so paper trades against real prices; writes are recorded but not filled.
Phase 1 keeps the book empty (no simulated positions) — real fill simulation and
the monitor land in Phase 2.
"""

from __future__ import annotations

from itertools import count

from hlcli.core.types import Network, OpenOrder, Order, OrderResult, Position
from hlcli.exchange.marks import MarksFeed, api_url


class PaperExchange:
    network = Network.PAPER

    def __init__(self, starting_equity: float, marks: MarksFeed | None = None) -> None:
        self._equity = starting_equity
        self._positions: list[Position] = []
        self._order_ids = count(1)
        # Paper reads public mainnet marks.
        self._marks = marks or MarksFeed(api_url(Network.PAPER))

    def get_marks(self) -> dict[str, float]:
        return self._marks.all_marks()

    def get_book(self, coin: str) -> dict | None:
        return self._marks.book(coin)

    def equity(self) -> float:
        return self._equity

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def get_open_orders(self) -> list[OpenOrder]:
        return []

    def place_order(self, order: Order) -> OrderResult:
        oid = f"paper-{next(self._order_ids)}"
        return OrderResult(accepted=True, status="recorded", order_id=oid, message="paper book")

    def cancel(self, coin: str, oid: int) -> OrderResult:
        return OrderResult(accepted=True, status="canceled", message="paper book")

    def cancel_all(self, coin: str | None = None) -> int:
        return 0

    def set_leverage(self, coin: str, leverage: int, *, cross: bool = True) -> OrderResult:
        return OrderResult(accepted=True, status="leverage_set", message=f"{coin} {leverage}x (paper)")
