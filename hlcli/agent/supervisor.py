"""The agent supervisor — one deterministic loop owning all cadences (PLAN.md §15.2).

The "agent" is this loop: plain code deciding *when* the existing passes run. LLM
calls stay exactly where they already are (decision, sentry manager, tuners). The
passes are injected callables, so the schedule is testable without an exchange,
a model, or real sleeps.

Last-run timestamps persist in the state store's meta table so `hl agent status`
works from another process, and the daily job survives restarts without re-running.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from hlcli.agent.intake_watch import IntakeResult
from hlcli.core.backoff import backoff_delay
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore

_HEARTBEAT_SECONDS = 3600.0
_FAILURE_ALERT_EVERY = 10  # alert on the 1st failure of a streak, then every Nth
_MAX_BACKOFF_SECONDS = 600.0

# meta keys `hl agent status` reads cross-process
LAST_TICK = "agent_last_tick_ts"
LAST_INTAKE = "agent_last_intake_ts"
LAST_EXEC = "agent_last_exec_ts"
LAST_SENTRY = "agent_last_sentry_ts"
LAST_DAILY = "agent_last_daily_date"


def _minutes_of_day(hhmm: str) -> int:
    try:
        hour, minute = hhmm.split(":")
        result = int(hour) * 60 + int(minute)
    except ValueError as exc:
        raise ValueError(f"HL_AGENT_DAILY_UTC must be HH:MM, got {hhmm!r}") from exc
    if not 0 <= result < 24 * 60:
        raise ValueError(f"HL_AGENT_DAILY_UTC must be a valid time of day, got {hhmm!r}")
    return result


@dataclass
class Cadence:
    intake_poll_seconds: float
    exec_interval_seconds: float
    sentry_interval_seconds: float
    daily_utc: str  # "HH:MM"


class Supervisor:
    """`tick()` is the unit of work (one poll + whatever passes are due);
    `run_forever` wraps it in sleep and failure backoff."""

    def __init__(
        self,
        state: StateStore,
        alerter: Alerter,
        cadence: Cadence,
        *,
        poll_intake: Callable[[], IntakeResult],
        exec_pass: Callable[[], object],
        sentry_pass: Callable[[], object],
        daily_pass: Callable[[], object],
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self._state = state
        self._alerter = alerter
        self._cadence = cadence
        self._daily_minute = _minutes_of_day(cadence.daily_utc)  # fail fast on a bad cap
        self._poll_intake = poll_intake
        self._exec_pass = exec_pass
        self._sentry_pass = sentry_pass
        self._daily_pass = daily_pass
        self._now = now_fn
        # Epoch zero ⇒ the first tick runs an exec + sentry pass immediately: a
        # startup self-check, and misfire recovery after downtime.
        self._last_exec = 0.0
        self._last_sentry = 0.0
        self._last_heartbeat = now_fn()
        self._ticks = 0

    def tick(self) -> list[str]:
        """Returns short labels for what ran — the CLI's per-tick note, and the tests' probe."""
        ran: list[str] = []
        now = self._now()
        self._ticks += 1

        intake = self._poll_intake()
        if intake.files:
            self._state.meta_set(LAST_INTAKE, str(now))
            ran.append(f"intake files={intake.files} enqueued={intake.enqueued} "
                       f"duplicates={intake.duplicates} failed={intake.failed}")

        # New candidates trade while fresh — don't wait out the exec cadence.
        if intake.enqueued or now - self._last_exec >= self._cadence.exec_interval_seconds:
            self._exec_pass()
            self._last_exec = now
            self._state.meta_set(LAST_EXEC, str(now))
            ran.append("exec")

        if now - self._last_sentry >= self._cadence.sentry_interval_seconds:
            self._sentry_pass()
            self._last_sentry = now
            self._state.meta_set(LAST_SENTRY, str(now))
            ran.append("sentry")

        today = self._daily_date_due(now)
        if today is not None:
            self._daily_pass()
            self._state.meta_set(LAST_DAILY, today)
            ran.append("daily")

        if now - self._last_heartbeat >= _HEARTBEAT_SECONDS:
            self._alerter.alert("agent_heartbeat", ticks=self._ticks)
            self._last_heartbeat = now

        self._state.meta_set(LAST_TICK, str(now))
        return ran

    def _daily_date_due(self, now: float) -> str | None:
        """Today's date string when the daily job is due and hasn't run today, else None.
        Meta-persisted, so a restart never re-runs it — and a start *after* the
        scheduled time on a fresh day still runs it (misfire recovery)."""
        utc = datetime.fromtimestamp(now, tz=timezone.utc)
        if utc.hour * 60 + utc.minute < self._daily_minute:
            return None
        today = utc.strftime("%Y-%m-%d")
        return None if self._state.meta_get(LAST_DAILY) == today else today

    def run_forever(
        self,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
        on_tick: Callable[[list[str]], None] | None = None,
        on_error: Callable[[int, Exception], None] | None = None,
    ) -> None:
        """Tick + sleep until interrupted. A failing tick backs off exponentially —
        a hard-down API isn't retried every few seconds forever at full LLM cost."""
        failures = 0
        while True:
            try:
                ran = self.tick()
                failures = 0
                if on_tick is not None:
                    on_tick(ran)
            except Exception as exc:  # keep the loop alive across transient LLM/feed faults
                failures += 1
                # Mark the loop alive even on a failing tick, so `agent status` reports
                # "running" (backing off) rather than "stopped" and no one restarts it.
                self._state.meta_set(LAST_TICK, str(self._now()))
                if failures == 1 or failures % _FAILURE_ALERT_EVERY == 0:
                    self._alerter.alert("agent_tick_failed", level="warning",
                                        consecutive=failures, error=str(exc))
                if on_error is not None:
                    on_error(failures, exc)
            sleep_fn(backoff_delay(self._cadence.intake_poll_seconds, failures, _MAX_BACKOFF_SECONDS))
