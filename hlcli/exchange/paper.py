"""Paper exchange — the default backend (PLAN.md §3).

A simulated, in-memory book that needs no keys and no signing libs. Phase 0 is a
clean stub: no positions, zero marks, orders are accepted into memory. Real fill
simulation (against public mainnet marks) and the monitor arrive in later phases.
"""

from __future__ import annotations

from itertools import count

from hlcli.core.types import Network, Order, Position
from hlcli.exchange.base import OrderResult


class PaperExchange:
    """In-memory simulated exchange. Implements the `Exchange` protocol."""

    network = Network.PAPER

    def __init__(self, starting_equity: float) -> None:
        self._equity = starting_equity
        self._positions: list[Position] = []
        self._order_ids = count(1)

    def get_marks(self) -> dict[str, float]:
        return {}  # Phase 1 wires public mainnet marks

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def equity(self) -> float:
        return self._equity

    def place_order(self, order: Order) -> OrderResult:
        order_id = f"paper-{next(self._order_ids)}"
        return OrderResult(accepted=True, order_id=order_id, message="recorded in paper book")
