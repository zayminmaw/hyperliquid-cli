"""Kill switch persistence + daily-loss-limit trip/reset."""

from hlcli.safety.breaker import Breaker
from hlcli.state.store import StateStore
from hlcli.tests._helpers import caps


def _breaker(tmp_path):
    state = StateStore(tmp_path / "state.db")
    return Breaker(state, caps(daily_loss_limit_pct=5.0)), state


def test_kill_switch_persists(tmp_path):
    b, state = _breaker(tmp_path)
    assert not b.tripped()
    b.set(True)
    assert b.tripped()
    assert Breaker(state, caps()).tripped()  # survives a fresh wrapper


def test_daily_loss_not_hit_within_limit(tmp_path):
    b, _ = _breaker(tmp_path)
    # day starts at 10000; down to 9600 = 4% < 5%
    assert b.daily_loss_hit(10_000.0, today="2026-06-27") is False
    assert b.daily_loss_hit(9_600.0, today="2026-06-27") is False


def test_daily_loss_hit_past_limit(tmp_path):
    b, _ = _breaker(tmp_path)
    b.daily_loss_hit(10_000.0, today="2026-06-27")  # set day start
    assert b.daily_loss_hit(9_400.0, today="2026-06-27") is True  # 6% >= 5%


def test_daily_loss_resets_next_day(tmp_path):
    b, _ = _breaker(tmp_path)
    b.daily_loss_hit(10_000.0, today="2026-06-27")
    assert b.daily_loss_hit(9_400.0, today="2026-06-27") is True
    # new day re-baselines at the lower equity -> not hit
    assert b.daily_loss_hit(9_400.0, today="2026-06-28") is False
