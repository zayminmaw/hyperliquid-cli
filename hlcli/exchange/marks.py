"""Marks + order-book feed with a short TTL cache (PLAN.md §11).

Reads hit the **public** Hyperliquid `/info` endpoint over `httpx` — no SDK, no
signing libs — so `paper` (which rides public mainnet marks) and the test suite
run without the exchange extra installed. The SDK is only needed to *sign* writes.
"""

from __future__ import annotations

import time

import httpx

from hlcli.core.types import Candle, Network

# Public REST endpoints (mirrors the SDK's constants, without importing it).
_API_URLS = {
    Network.PAPER: "https://api.hyperliquid.xyz",  # paper rides public mainnet marks
    Network.MAINNET: "https://api.hyperliquid.xyz",
    Network.TESTNET: "https://api.hyperliquid-testnet.xyz",
}

# candleSnapshot wants a [startTime, endTime] window in ms, so we derive the
# window from `lookback × interval` rather than asking for a bar count directly.
_INTERVAL_MS = {
    "1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000,
    "1h": 3_600_000, "4h": 14_400_000, "1d": 86_400_000,
}


def api_url(network: Network) -> str:
    return _API_URLS[network]


class MarksFeed:
    def __init__(self, base_url: str, *, ttl_seconds: float = 2.0, timeout: float = 10.0) -> None:
        self._ttl = ttl_seconds
        self._client = httpx.Client(base_url=base_url, timeout=timeout)
        self._cache: dict[str, float] = {}
        self._fetched_at = 0.0
        self._sz_decimals: dict[str, int] | None = None  # static per session; fetched once

    def _info(self, body: dict) -> dict:
        response = self._client.post("/info", json=body)
        response.raise_for_status()  # an HTTP error shouldn't surface as a parse error downstream
        return response.json()

    def all_marks(self, *, force: bool = False) -> dict[str, float]:
        now = time.monotonic()
        if not force and self._cache and (now - self._fetched_at) < self._ttl:
            return dict(self._cache)
        mids = self._info({"type": "allMids"})
        self._cache = {coin: float(px) for coin, px in mids.items()}
        self._fetched_at = now
        return dict(self._cache)  # a copy — callers must not mutate the cache

    def sz_decimals(self, coin: str) -> int | None:
        """The asset's size precision from the exchange `meta` universe, or None for an
        unknown coin. Fetched once per session (static exchange metadata)."""
        if self._sz_decimals is None:
            meta = self._info({"type": "meta"})
            self._sz_decimals = {
                a["name"]: int(a["szDecimals"]) for a in meta.get("universe", [])
            }
        return self._sz_decimals.get(coin)

    def mark(self, coin: str) -> float | None:
        return self.all_marks().get(coin)

    def book(self, coin: str) -> dict | None:
        return self._info({"type": "l2Book", "coin": coin})

    def candles(self, coin: str, *, interval: str = "15m", lookback: int = 48) -> list[Candle]:
        """The last `lookback` OHLCV bars for `coin` at `interval` (uncached; the
        executor pulls these once per coin per pass). Empty list if the feed has none."""
        interval_ms = _INTERVAL_MS.get(interval)
        if interval_ms is None:
            raise ValueError(f"unsupported candle interval {interval!r}; expected one of {', '.join(_INTERVAL_MS)}")
        end = int(time.time() * 1000)
        start = end - lookback * interval_ms
        raw = self._info(
            {"type": "candleSnapshot", "req": {"coin": coin, "interval": interval, "startTime": start, "endTime": end}}
        )
        return [
            Candle(t=int(c["t"]), o=float(c["o"]), h=float(c["h"]), l=float(c["l"]), c=float(c["c"]), v=float(c["v"]))
            for c in (raw or [])
        ]
