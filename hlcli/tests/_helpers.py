"""Shared test helpers — fake marks + caps/tunable factories (no env, no network)."""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig, clamp


class FakeMarks:
    """Stand-in for MarksFeed with fixed prices — keeps paper fills network-free."""

    def __init__(self, marks: dict[str, float] | None = None) -> None:
        self._m = marks or {"BTC": 100.0, "ETH": 1500.0, "SOL": 50.0}

    def all_marks(self, *, force: bool = False) -> dict[str, float]:
        return self._m

    def mark(self, coin: str) -> float | None:
        return self._m.get(coin)

    def book(self, coin: str) -> dict:
        return {"coin": coin, "levels": [[], []]}


def caps(**kw) -> Caps:
    base = dict(
        allowed_coins="BTC,ETH,SOL", starting_equity=10_000.0, max_notional_per_trade=1_000.0,
        max_concurrent_positions=3, max_leverage=3.0, rr_floor=1.5, max_signal_age_minutes=30,
        daily_loss_limit_pct=5.0,
    )
    return Caps(**{**base, **kw})


def tunable() -> TunableConfig:
    return clamp(TunableConfig())
