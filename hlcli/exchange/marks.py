"""Marks + order-book feed with a short TTL cache (PLAN.md §11).

Reads hit the **public** Hyperliquid `/info` endpoint over `httpx` — no SDK, no
signing libs — so `paper` (which rides public mainnet marks) and the test suite
run without the exchange extra installed. The SDK is only needed to *sign* writes.
"""

from __future__ import annotations

import time

import httpx

from hlcli.core.types import Network

# Public REST endpoints (mirrors the SDK's constants, without importing it).
_API_URLS = {
    Network.PAPER: "https://api.hyperliquid.xyz",  # paper rides public mainnet marks
    Network.MAINNET: "https://api.hyperliquid.xyz",
    Network.TESTNET: "https://api.hyperliquid-testnet.xyz",
}


def api_url(network: Network) -> str:
    return _API_URLS[network]


class MarksFeed:
    def __init__(self, base_url: str, *, ttl_seconds: float = 2.0, timeout: float = 10.0) -> None:
        self._ttl = ttl_seconds
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._cache: dict[str, float] = {}
        self._fetched_at = 0.0

    def _info(self, body: dict) -> dict:
        return self._client.post("/info", json=body).json()

    def all_marks(self, *, force: bool = False) -> dict[str, float]:
        now = time.monotonic()
        if not force and self._cache and (now - self._fetched_at) < self._ttl:
            return self._cache
        mids = self._info({"type": "allMids"})
        self._cache = {coin: float(px) for coin, px in mids.items()}
        self._fetched_at = now
        return self._cache

    def mark(self, coin: str) -> float | None:
        return self.all_marks().get(coin)

    def book(self, coin: str) -> dict | None:
        return self._info({"type": "l2Book", "coin": coin})
