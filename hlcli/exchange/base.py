"""The exchange interface every backend implements (paper / testnet / mainnet).

A `Protocol`, not an ABC: backends are matched structurally, and the live backend's
SDK/signing deps stay lazy-imported in their own module. Reads (marks/book/
positions/orders/equity) work for every backend; writes (order/cancel/leverage)
require credentials and raise on a read-only backend.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hlcli.core.types import Candle, Fill, Network, OpenOrder, Order, OrderResult, Position


@runtime_checkable
class Exchange(Protocol):
    network: Network

    # --- reads ---
    def get_marks(self) -> dict[str, float]:
        """Coin → current mark price (may be empty)."""
        ...

    def get_book(self, coin: str) -> dict | None:
        """L2 order book snapshot for a coin, or None if unavailable."""
        ...

    def get_candles(self, coin: str, *, interval: str = "15m", lookback: int = 48) -> list[Candle]:
        """Recent OHLCV bars for a coin (may be empty)."""
        ...

    def equity(self) -> float:
        """Account equity used for sizing/reporting."""
        ...

    def maintenance_margin(self) -> float:
        """Cross maintenance margin currently required (USDC) — the liquidation threshold
        for the whole book. 0.0 when the backend has no positions / doesn't report it (paper)."""
        ...

    def get_positions(self) -> list[Position]: ...

    def get_open_orders(self) -> list[OpenOrder]: ...

    def recent_fills(self, since_ms: int) -> list[Fill]:
        """Executions since `since_ms` (epoch ms), oldest→newest. Empty on backends
        with no fill feed (paper realizes at the level, so it has none)."""
        ...

    # --- writes ---
    def place_order(self, order: Order) -> OrderResult: ...

    def cancel(self, coin: str, oid: int) -> OrderResult: ...

    def cancel_all(self, coin: str | None = None) -> int: ...

    def set_leverage(self, coin: str, leverage: int, *, cross: bool = True) -> OrderResult: ...
