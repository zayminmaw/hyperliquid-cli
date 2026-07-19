"""Kill switch + daily-loss-limit (PLAN.md §5, §7).

Both halt *new* fires; open positions are still managed. The kill switch is a
persisted manual toggle. The daily-loss-limit trips automatically when equity has
drawn down past `DAILY_LOSS_LIMIT_PCT` from the day's starting equity (tracked in
state, reset on date rollover).

The equity fed in is **mark-to-market on both backends** (audit X-4): live equity is
Hyperliquid's `accountValue` (includes unrealized P&L) and paper equity is
starting + realized + unrealized at current marks — so an open position's drawdown
alone can trip the limit; nothing has to be realized first. The check runs on every
executor *and* sentry pass. Between passes, the native per-position stops are what
bound loss — this breaker is the portfolio-level, pass-granular bound, not a
tick-level one.
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

    def daily_loss_hit(self, equity: float, *, today: str | None = None, persist: bool = True) -> bool:
        """True once today's drawdown from the day-start equity hits the limit.

        `persist=False` (dry-run) never writes the day-rollover state — the preview
        stays side-effect-free; a fresh day then simply reads as zero drawdown."""
        today = today or date.today().isoformat()
        if self._state.meta_get(_DAY_KEY) != today:
            if not persist:
                return False  # new day, nothing recorded: drawdown is zero by definition
            self._state.meta_set(_DAY_KEY, today)
            self._state.meta_set(_DAY_START_EQUITY_KEY, str(equity))

        start = float(self._state.meta_get(_DAY_START_EQUITY_KEY, str(equity)))
        if start <= 0:
            return False
        drawdown_pct = (start - equity) / start * 100.0
        return drawdown_pct >= self._caps.daily_loss_limit_pct
