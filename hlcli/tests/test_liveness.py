"""Runner-liveness classification (audit F) — fail-closed staleness verdicts."""

from hlcli.agent.liveness import Liveness, classify, stale_after_seconds


def test_no_heartbeat_is_never():
    assert classify(None, 100.0) is Liveness.NEVER


def test_fresh_tick_is_alive():
    assert classify(50.0, 100.0) is Liveness.ALIVE


def test_boundary_age_is_still_alive():
    # exactly at the threshold counts as alive — a tick landing right on the margin isn't dead.
    assert classify(100.0, 100.0) is Liveness.ALIVE


def test_old_tick_is_stale():
    assert classify(150.0, 100.0) is Liveness.STALE


def test_stale_after_derives_from_poll_by_default():
    assert stale_after_seconds(60.0) == 180.0  # 3× the poll interval
    assert stale_after_seconds(60.0, 0.0) == 180.0  # 0 override ⇒ derive


def test_stale_after_override_wins():
    assert stale_after_seconds(60.0, 500.0) == 500.0
