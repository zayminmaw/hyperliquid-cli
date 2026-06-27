"""The exchange interface every backend implements (paper / testnet / mainnet).

A `Protocol`, not an ABC: backends are matched structurally, and the LLM/exchange
deps for the live backends stay lazy-imported in their own modules. Phase 0 defines
the minimal surface the skeleton needs; later phases extend it (orders, cancels,
leverage, SL/TP triggers).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hlcli.core.types import Network, Order, Position


@runtime_checkable
class Exchange(Protocol):
    network: Network

    def get_marks(self) -> dict[str, float]:
        """Coin → current mark price for all tradable coins (may be empty)."""
        ...

    def get_positions(self) -> list[Position]:
        """Currently open positions."""
        ...

    def equity(self) -> float:
        """Account equity used for sizing."""
        ...

    def place_order(self, order: Order) -> OrderResult: ...


class OrderResult:
    """Outcome of a `place_order` call. Real fill modelling lands in later phases."""

    def __init__(self, *, accepted: bool, order_id: str | None = None, message: str = "") -> None:
        self.accepted = accepted
        self.order_id = order_id
        self.message = message
