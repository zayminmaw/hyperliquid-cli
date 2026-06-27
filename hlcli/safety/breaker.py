"""Kill switch + daily-loss-limit (PLAN.md §5, §7).

Both halt *new* fires; open positions are still managed. The kill switch is a
persisted manual toggle. The daily-loss-limit trips automatically when equity has
drawn down past `DAILY_LOSS_LIMIT_PCT` from the day's starting equity (tracked in
state, reset on date rollover).
"""

from __future__ import annotations

from datetime import date

from hlcli.core.config import Caps
from hlcli.state.store import StateStore

_DAY_KEY = "breaker_day"
_DAY_START_EQUITY_KEY = "day_start_equity"


class Breaker:
    def __init__(self, state: StateStore, caps: Caps) -> None:
        self._state = state
        self._caps = caps

    def tripped(self) -> bool:
        """Manual kill switch state."""
        return self._state.breaker_tripped()

    def set(self, on: bool) -> None:
        self._state.set_breaker(on)

    def daily_loss_hit(self, equity: float, *, today: str | None = None) -> bool:
        """True once today's drawdown from the day-start equity hits the limit."""
        today = today or date.today().isoformat()
        if self._state.meta_get(_DAY_KEY) != today:
            self._state.meta_set(_DAY_KEY, today)
            self._state.meta_set(_DAY_START_EQUITY_KEY, str(equity))

        start = float(self._state.meta_get(_DAY_START_EQUITY_KEY, str(equity)))
        if start <= 0:
            return False
        drawdown_pct = (start - equity) / start * 100.0
        return drawdown_pct >= self._caps.daily_loss_limit_pct
