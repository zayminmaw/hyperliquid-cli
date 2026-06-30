"""Native exchange-side SL/TP (PLAN.md §7): protective-order construction, the
all-or-nothing placement, and the runner's hard prerequisite — an entry that can't
be protected is emergency-closed, never left naked."""

from hlcli.core.types import Candidate, Network, OrderResult, OrderType, Side
from hlcli.executor.protect import (
    place_protection,
    protective_orders,
    requires_native_protection,
)
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import act_now, caps, tunable

NOW = 1_000_000.0


class FakeLiveExchange:
    """A live-network backend stand-in: fills entries, optionally rejects triggers."""

    def __init__(self, network=Network.TESTNET, marks=None, *, fail_triggers=False):
        self.network = network
        self._marks = marks or {"BTC": 100.0}
        self.fail_triggers = fail_triggers
        self.placed = []

    def get_marks(self):
        return dict(self._marks)

    def get_book(self, coin):
        return None

    def equity(self):
        return 10_000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return []

    def place_order(self, order):
        self.placed.append(order)
        is_trigger = order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT)
        if self.fail_triggers and is_trigger:
            return OrderResult(accepted=False, status="error", message="trigger rejected")
        return OrderResult(accepted=True, status="filled", order_id="x")

    def cancel(self, *a, **k):
        return OrderResult(accepted=True, status="canceled")

    def cancel_all(self, *a, **k):
        return 0

    def set_leverage(self, *a, **k):
        return OrderResult(accepted=True, status="leverage_set")


class CapturingAlerter:
    def __init__(self):
        self.events = []

    def alert(self, event, *, level="info", **fields):
        record = {"event": event, "level": level, **fields}
        self.events.append(record)
        return record


def _cand(coin="BTC", side=Side.LONG, entry=100.0, tp=120.0, sl=90.0) -> Candidate:
    return Candidate(id="a", coin=coin, side=side, entry=entry, tp=tp, sl=sl, created_at=NOW)


# --- pure builders ---

def test_protection_scope_excludes_only_paper():
    assert requires_native_protection(Network.MAINNET)
    assert requires_native_protection(Network.TESTNET)
    assert not requires_native_protection(Network.PAPER)


def test_protective_orders_are_reduce_only_triggers_on_closing_side():
    sl, tp = protective_orders(_cand(side=Side.LONG), size=2.0)
    assert sl.order_type is OrderType.STOP_LOSS and sl.trigger_price == 90.0
    assert tp.order_type is OrderType.TAKE_PROFIT and tp.trigger_price == 120.0
    assert sl.side is Side.SHORT and tp.side is Side.SHORT  # closing a long
    assert sl.reduce_only and tp.reduce_only and sl.size == 2.0


def test_short_protection_closes_with_a_long():
    sl, tp = protective_orders(_cand(side=Side.SHORT, entry=100, tp=80, sl=110), size=1.0)
    assert sl.side is Side.LONG and tp.side is Side.LONG


def test_place_protection_fails_fast_on_first_rejection():
    ex = FakeLiveExchange(fail_triggers=True)
    result = place_protection(ex, _cand(), size=1.0)
    assert not result.ok and result.failed == "trigger rejected"
    assert len(result.placed) == 1  # stopped at the stop-loss; no half-protection


# --- runner: the hard prerequisite has teeth ---

def test_protected_fire_places_entry_plus_two_triggers(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert (s.fired, s.rejected) == (1, 0)
    kinds = [o.order_type for o in ex.placed]
    assert kinds == [OrderType.LIMIT, OrderType.STOP_LOSS, OrderType.TAKE_PROFIT]
    assert len(state.open_trades()) == 1
    assert [e["event"] for e in alerter.events] == ["fire"]


def test_unprotectable_entry_is_emergency_closed_not_left_naked(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fail_triggers=True)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert (s.fired, s.rejected) == (0, 1)
    assert state.open_trades() == []  # never recorded as a live trade
    # entry → stop-loss (rejected) → market reduce-only flatten
    assert ex.placed[-1].order_type is OrderType.MARKET and ex.placed[-1].reduce_only
    crit = [e for e in alerter.events if e["event"] == "protection_failed"]
    assert crit and crit[0]["level"] == "critical" and crit[0]["emergency_closed"]


def test_paper_fire_skips_native_protection(tmp_path):
    # Paper relies on the resolver, so a paper fire places no protective triggers.
    from hlcli.exchange.paper import PaperExchange
    from hlcli.tests._helpers import FakeMarks

    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks({"BTC": 100.0}), state=state)
    state.enqueue(_cand())
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert s.fired == 1 and len(state.open_trades()) == 1
