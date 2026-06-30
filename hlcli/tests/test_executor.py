"""End-to-end executor pass: candidates → paper fills, deterministic + restart-safe."""

from hlcli.core.config_schema import RegimeGate, TunableConfig, clamp
from hlcli.core.types import Candidate, Candle, Order, OrderResult, OrderType, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.execute import fire
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, act_wait, caps, tunable

NOW = 1_000_000.0


def _setup(tmp_path, marks=None):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    return ex, state


def _cand(id, coin="BTC", entry=100.0, tp=120.0, sl=90.0) -> Candidate:
    return Candidate(id=id, coin=coin, side=Side.LONG, entry=entry, tp=tp, sl=sl, created_at=NOW)


def test_candidate_flows_to_paper_fill(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert (s.seen, s.fired, s.rejected) == (1, 1, 0)
    assert [p.coin for p in ex.get_positions()] == ["BTC"]


def test_restart_does_not_refire(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)

    ex2 = PaperExchange(10_000.0, marks=FakeMarks(), state=state)  # "restart"
    s2 = run_once(ex2, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert (s2.seen, s2.fired) == (0, 0)
    assert len(ex2.get_positions()) == 1  # one position, not two


def test_idempotent_skip_on_reprocess(tmp_path):
    ex, state = _setup(tmp_path)
    c = _cand("a")
    state.enqueue(c)
    order = Order(coin="BTC", side=Side.LONG, order_type=OrderType.LIMIT, size=1.0, price=100.0)
    fire(ex, state, c, order, NOW)
    repeat = fire(ex, state, c, order, NOW)  # crash-before-advance simulation
    assert repeat.status == "duplicate"
    assert len(ex.get_positions()) == 1


class _RejectingExchange:
    """Definitively refuses every order (a clean reject, not a transport error)."""

    def place_order(self, order):
        return OrderResult(accepted=False, status="error", message="insufficient margin")


def test_clean_reject_releases_idempotency_key(tmp_path):
    # A definitive reject means nothing reached the book, so the key store must not
    # claim the candidate fired — otherwise a re-enqueue would be wrongly skipped.
    _ex, state = _setup(tmp_path)
    c = _cand("a")
    result = fire(_RejectingExchange(), state, c, _cand_order(), NOW)
    assert not result.accepted
    assert not state.already_fired("a")  # released — free to retry


def _cand_order() -> Order:
    return Order(coin="BTC", side=Side.LONG, order_type=OrderType.MARKET, size=1.0)


def test_dry_run_is_side_effect_free(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, dry_run=True, now=NOW)
    assert (s.approved, s.fired) == (1, 0)
    assert ex.get_positions() == []
    assert state.get_hwm() == 0  # stream untouched
    assert run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW).fired == 1  # real pass fires it


def test_one_per_coin_within_pass(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a", coin="BTC"))
    state.enqueue(_cand("b", coin="BTC"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert (s.fired, s.rejected) == (1, 1)


def test_max_concurrent(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a", coin="BTC"))
    state.enqueue(_cand("b", coin="ETH", entry=1500, tp=1800, sl=1400))
    s = run_once(ex, state, caps(max_concurrent_positions=1), tunable(), decide_fn=act_now, now=NOW)
    assert (s.fired, s.rejected) == (1, 1)


def test_breaker_blocks_fire(tmp_path):
    ex, state = _setup(tmp_path)
    state.set_breaker(True)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert (s.fired, s.rejected) == (0, 1)


class _TrendingMarks(FakeMarks):
    """FakeMarks that also serves a clean uptrend, so the runner computes regime='trend'."""

    def candles(self, coin, *, interval="15m", lookback=48):
        return [Candle(t=i, o=100 + i, h=100 + i, l=100 + i, c=100 + i, v=1.0) for i in range(24)]


def test_candle_regime_reaches_the_gate(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=_TrendingMarks(), state=state)
    state.enqueue(_cand("a"))  # would fire on its own merits
    tun = clamp(TunableConfig(regime=RegimeGate(enabled=True, allowed_regimes=("range",))))
    s = run_once(ex, state, caps(), tun, decide_fn=act_now, now=NOW)
    assert (s.fired, s.rejected) == (0, 1)  # computed regime 'trend' not allowed → gate rejects


# --- wait → follow-up loop ---

def test_wait_defers_instead_of_rejecting(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_wait(), now=NOW)
    assert (s.fired, s.rejected, s.deferred) == (0, 0, 1)
    assert state.deferred_count() == 1
    assert ex.get_positions() == []  # nothing fired — parked for a later look


def test_due_deferral_fires_on_recheck(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    run_once(ex, state, caps(), tunable(), decide_fn=act_wait(minutes=1), now=NOW)  # → deferred
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW + 120)    # re-check says now
    assert (s.seen, s.rechecked, s.fired) == (0, 1, 1)
    assert state.deferred_count() == 0
    assert [p.coin for p in ex.get_positions()] == ["BTC"]


def test_recheck_attempts_exhaust(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    cap2 = caps(followup_max_attempts=2)
    run_once(ex, state, cap2, tunable(), decide_fn=act_wait(minutes=1), now=NOW)  # park, attempts=2
    s1 = run_once(ex, state, cap2, tunable(), decide_fn=act_wait(minutes=1), now=NOW + 120)
    assert (s1.rechecked, s1.deferred) == (1, 1) and state.deferred_count() == 1  # re-parked, attempts=1
    s2 = run_once(ex, state, cap2, tunable(), decide_fn=act_wait(minutes=1), now=NOW + 240)
    assert (s2.rechecked, s2.deferred, s2.rejected) == (1, 0, 1)  # budget spent → terminal reject
    assert state.deferred_count() == 0


def test_recheck_clamped_within_freshness(tmp_path):
    ex, state = _setup(tmp_path)
    c = _cand("a")
    state.enqueue(c)
    run_once(ex, state, caps(max_signal_age_minutes=30), tunable(), decide_fn=act_wait(minutes=1000), now=NOW)
    due = state.due_deferred(NOW + 10**9)  # fetch the parked row irrespective of time
    assert len(due) == 1
    assert due[0].next_check_at == c.created_at + 30 * 60  # clamped to the freshness boundary


def test_wait_rejected_when_no_freshness_room(tmp_path):
    ex, state = _setup(tmp_path)
    c = _cand("a")
    state.enqueue(c)
    near_stale = c.created_at + 30 * 60 - 30  # 30s before stale — under the min re-check gap
    s = run_once(ex, state, caps(max_signal_age_minutes=30), tunable(), decide_fn=act_wait(minutes=1), now=near_stale)
    assert (s.deferred, s.rejected) == (0, 1)
    assert state.deferred_count() == 0


def test_wait_with_followups_disabled_rejects(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    s = run_once(ex, state, caps(followup_max_attempts=0), tunable(), decide_fn=act_wait(), now=NOW)
    assert (s.deferred, s.rejected) == (0, 1)  # feature off → wait is a terminal reject
    assert state.deferred_count() == 0


def test_breaker_freezes_recheck(tmp_path):
    ex, state = _setup(tmp_path)
    state.enqueue(_cand("a"))
    run_once(ex, state, caps(), tunable(), decide_fn=act_wait(minutes=1), now=NOW)  # parked
    state.set_breaker(True)
    frozen = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW + 120)
    assert (frozen.rechecked, frozen.fired) == (0, 0)  # kill switch → no re-check
    assert state.deferred_count() == 1  # still parked, attempt not consumed
    state.set_breaker(False)
    thawed = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW + 180)
    assert (thawed.rechecked, thawed.fired) == (1, 1)  # cleared → re-checked and fires
    assert state.deferred_count() == 0
