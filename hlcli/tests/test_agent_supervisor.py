"""Supervisor scheduling (PLAN.md §15.2): startup pass, cadences, intake-triggered
exec, restart-safe daily job, failure backoff — all on an injected clock."""

from __future__ import annotations

import io
from datetime import datetime, timezone

import pytest

from hlcli.agent.intake_watch import IntakeResult
from hlcli.agent.supervisor import LAST_DAILY, LAST_EXEC, Cadence, Supervisor
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore


def utc(hour: int, minute: int, day: int = 7) -> float:
    return datetime(2026, 7, day, hour, minute, tzinfo=timezone.utc).timestamp()


class Clock:
    def __init__(self, t: float) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


class Recorder:
    """Injected in place of a real pass; counts invocations."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> None:
        self.calls += 1


CADENCE = Cadence(intake_poll_seconds=5.0, exec_interval_seconds=300.0,
                  sentry_interval_seconds=60.0, daily_utc="00:10")


def make_supervisor(tmp_path, clock, *, intake=IntakeResult(), cadence=CADENCE, state=None):
    state = state or StateStore(tmp_path / "state.db")
    stream = io.StringIO()
    passes = {"exec": Recorder(), "sentry": Recorder(), "daily": Recorder()}
    sup = Supervisor(
        state, Alerter(stream=stream), cadence,
        poll_intake=lambda: intake,
        exec_pass=passes["exec"], sentry_pass=passes["sentry"], daily_pass=passes["daily"],
        now_fn=clock,
    )
    return sup, passes, state, stream


def test_first_tick_runs_exec_and_sentry_and_persists_timestamps(tmp_path):
    clock = Clock(utc(12, 0))
    sup, passes, state, _ = make_supervisor(tmp_path, clock)

    ran = sup.tick()

    assert passes["exec"].calls == 1 and passes["sentry"].calls == 1
    assert "exec" in ran and "sentry" in ran
    assert float(state.meta_get(LAST_EXEC)) == clock.t


def test_cadences_hold_between_passes(tmp_path):
    clock = Clock(utc(12, 0))
    sup, passes, _, _ = make_supervisor(tmp_path, clock)
    sup.tick()

    clock.t += 30  # under both intervals
    assert sup.tick() == []

    clock.t += 40  # sentry (60s) now due, exec (300s) not
    assert sup.tick() == ["sentry"]
    assert passes["exec"].calls == 1 and passes["sentry"].calls == 2

    clock.t += 300
    assert set(sup.tick()) == {"exec", "sentry"}


def test_new_intake_triggers_exec_immediately(tmp_path):
    clock = Clock(utc(12, 0))
    sup, passes, _, _ = make_supervisor(
        tmp_path, clock, intake=IntakeResult(files=1, enqueued=2))
    sup.tick()

    clock.t += 5  # far inside the exec cadence — the new candidates must not wait
    ran = sup.tick()

    assert "exec" in ran
    assert passes["exec"].calls == 2


def test_duplicate_only_files_do_not_trigger_exec(tmp_path):
    clock = Clock(utc(12, 0))
    sup, passes, _, _ = make_supervisor(
        tmp_path, clock, intake=IntakeResult(files=1, enqueued=0, duplicates=2))
    sup.tick()

    clock.t += 5
    ran = sup.tick()

    assert passes["exec"].calls == 1
    assert any(r.startswith("intake") for r in ran)


def test_daily_job_runs_once_per_utc_day_and_survives_restart(tmp_path):
    clock = Clock(utc(0, 5))  # before the 00:10 schedule
    sup, passes, state, _ = make_supervisor(tmp_path, clock)
    sup.tick()
    assert passes["daily"].calls == 0

    clock.t = utc(0, 15)
    sup.tick()
    assert passes["daily"].calls == 1
    assert state.meta_get(LAST_DAILY) == "2026-07-07"

    clock.t = utc(18, 0)
    sup.tick()
    assert passes["daily"].calls == 1  # not twice on the same day

    # restart mid-day: the meta record, not process memory, is the guard
    sup2, passes2, _, _ = make_supervisor(tmp_path, clock, state=state)
    sup2.tick()
    assert passes2["daily"].calls == 0

    clock.t = utc(0, 30, day=8)  # a start AFTER the scheduled time still runs it
    sup2.tick()
    assert passes2["daily"].calls == 1
    assert state.meta_get(LAST_DAILY) == "2026-07-08"


def test_bad_daily_utc_fails_at_construction(tmp_path):
    bad = Cadence(5.0, 300.0, 60.0, daily_utc="25:99")
    with pytest.raises(ValueError, match="HL_AGENT_DAILY_UTC"):
        make_supervisor(tmp_path, Clock(utc(12, 0)), cadence=bad)


def test_heartbeat_alert_lands_hourly(tmp_path):
    clock = Clock(utc(12, 0))
    sup, _, _, stream = make_supervisor(tmp_path, clock)
    sup.tick()
    assert "agent_heartbeat" not in stream.getvalue()

    clock.t += 3600
    sup.tick()
    assert "agent_heartbeat" in stream.getvalue()


def test_run_forever_backs_off_and_alerts_on_failing_ticks(tmp_path):
    clock = Clock(utc(12, 0))
    state = StateStore(tmp_path / "state.db")
    stream = io.StringIO()

    def boom() -> None:
        raise RuntimeError("api down")

    sup = Supervisor(
        state, Alerter(stream=stream), CADENCE,
        poll_intake=boom, exec_pass=boom, sentry_pass=boom, daily_pass=boom,
        now_fn=clock,
    )

    sleeps: list[float] = []
    errors: list[int] = []

    def sleep_fn(seconds: float) -> None:
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise KeyboardInterrupt  # stop the loop from the test

    with pytest.raises(KeyboardInterrupt):
        sup.run_forever(sleep_fn=sleep_fn, on_error=lambda n, exc: errors.append(n))

    assert errors == [1, 2, 3]
    assert sleeps == [10.0, 20.0, 40.0]  # 5s poll base doubling per consecutive failure
    assert "agent_tick_failed" in stream.getvalue()
