"""Native exchange-side SL/TP (PLAN.md §7): protective-order construction, the
all-or-nothing placement, and the runner's hard prerequisite — an entry that can't
be protected is emergency-closed, never left naked."""

from hlcli.core.types import Candidate, Network, OrderResult, OrderType, Side
from hlcli.executor.protect import (
    place_protection,
    place_reduce_only,
    protective_orders,
    requires_native_protection,
)
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import act_now, caps, tunable

NOW = 1_000_000.0


class FakeLiveExchange:
    """A live-network backend stand-in: fills entries, optionally rejects triggers."""

    def __init__(self, network=Network.TESTNET, marks=None, *, fail_triggers=False,
                 fail_close=False, fill_size=None, fill_price=None, positions=None, open_orders=None,
                 fills=None):
        self.network = network
        self._marks = marks or {"BTC": 100.0}
        self.fills = fills or []  # Fill list returned by recent_fills (item L)
        self.fail_triggers = fail_triggers
        # fail_close: True rejects the emergency market-close; "raise" makes it raise a
        # backend error (the transport-unknown flatten). Both must yield abort_failed.
        self.fail_close = fail_close
        self.fill_size = fill_size  # None = fill the whole order
        self.fill_price = fill_price
        self.positions = positions or []
        self.open_orders = open_orders or []
        self.placed = []
        self.canceled = []

    def get_marks(self):
        return dict(self._marks)

    def get_book(self, coin):
        return None

    def get_candles(self, coin, *, interval="15m", lookback=48):
        return []

    def recent_fills(self, since_ms):
        return [f for f in self.fills if f.time_ms >= since_ms]

    def equity(self):
        return 10_000.0

    def get_positions(self):
        return list(self.positions)

    def get_open_orders(self):
        return list(self.open_orders)

    def place_order(self, order):
        self.placed.append(order)
        oid = str(len(self.placed))  # numeric, like the real exchange
        is_trigger = order.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT)
        # fail_triggers: True rejects every trigger; "tp" rejects only the take-profit
        # (the partial-protection case — an SL already resting when the abort happens).
        rejected = (self.fail_triggers is True and is_trigger) or (
            self.fail_triggers == "tp" and order.order_type is OrderType.TAKE_PROFIT
        )
        if rejected:
            return OrderResult(accepted=False, status="error", message="trigger rejected")
        is_close = order.order_type is OrderType.MARKET and order.reduce_only
        if is_close and self.fail_close:
            if self.fail_close == "raise":
                raise RuntimeError("emergency close transport error")
            return OrderResult(accepted=False, status="error", message="close rejected")
        mark = self._marks.get(order.coin)
        price = self.fill_price if self.fill_price is not None else mark
        if is_trigger or order.reduce_only:
            return OrderResult(accepted=True, status="filled", order_id=oid,
                               filled_size=order.size, avg_price=price)
        filled = order.size if self.fill_size is None else self.fill_size
        return OrderResult(accepted=True, status="filled" if filled > 0 else "resting", order_id=oid,
                           filled_size=filled, avg_price=price if filled > 0 else None)

    def cancel(self, coin, oid):
        self.canceled.append((coin, oid))
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


# --- D-2: bounded retry on the reduce-only order-writes ---

def test_place_reduce_only_retries_transient_then_succeeds(monkeypatch):
    monkeypatch.setattr("hlcli.executor.protect._sleep", lambda *_: None)
    calls = {"n": 0}

    class Flaky:
        def place_order(self, order):
            calls["n"] += 1
            if calls["n"] < 3:
                raise RuntimeError("429 rate limited")  # transport/rate-limit → retry
            return OrderResult(accepted=True, status="filled", order_id="9",
                               filled_size=order.size, avg_price=100.0)

    res = place_reduce_only(Flaky(), protective_orders(_cand(), size=1.0)[0])
    assert res.accepted and calls["n"] == 3


def test_place_reduce_only_does_not_retry_a_definitive_reject(monkeypatch):
    monkeypatch.setattr("hlcli.executor.protect._sleep", lambda *_: None)
    calls = {"n": 0}

    class Rejecter:
        def place_order(self, order):
            calls["n"] += 1
            return OrderResult(accepted=False, status="error", message="min notional")

    res = place_reduce_only(Rejecter(), protective_orders(_cand(), size=1.0)[0])
    assert not res.accepted and calls["n"] == 1  # a real 'no' is answered once, never retried


def test_place_reduce_only_exhausts_retries_to_a_definitive_non_placement(monkeypatch):
    monkeypatch.setattr("hlcli.executor.protect._sleep", lambda *_: None)
    calls = {"n": 0}

    class AlwaysDown:
        def place_order(self, order):
            calls["n"] += 1
            raise RuntimeError("connection reset")

    res = place_reduce_only(AlwaysDown(), protective_orders(_cand(), size=1.0)[0])
    assert not res.accepted and "exhausted retries" in res.message and calls["n"] == 3


# --- runner: the hard prerequisite has teeth ---

def test_protected_fire_places_entry_plus_two_triggers(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert (s.fired, s.rejected) == (1, 0)
    kinds = [o.order_type for o in ex.placed]
    assert kinds == [OrderType.MARKET, OrderType.STOP_LOSS, OrderType.TAKE_PROFIT]  # marketable entry
    assert len(state.open_trades()) == 1
    assert [e["event"] for e in alerter.events] == ["fire"]


def test_open_trade_and_protection_use_the_actual_filled_size(tmp_path):
    # A partial fill at a slipped price: ledger + protective triggers track reality,
    # not the intended order size/price.
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fill_size=0.5, fill_price=101.0)
    state.enqueue(_cand())
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)

    trade = state.open_trades()[0]
    assert trade["size"] == 0.5 and trade["entry"] == 101.0
    triggers = [o for o in ex.placed if o.order_type in (OrderType.STOP_LOSS, OrderType.TAKE_PROFIT)]
    assert triggers and all(o.size == 0.5 for o in triggers)


def test_accepted_but_unfilled_entry_opens_no_trade(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fill_size=0.0)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert (s.fired, s.failed, s.rejected) == (0, 1, 0)  # exchange failure, not a gate reject
    assert state.open_trades() == []
    assert [o.order_type for o in ex.placed] == [OrderType.MARKET]  # no protection on a non-position
    assert any(e["event"] == "reject" and e["reason"] == "unfilled" for e in alerter.events)


def test_unprotectable_entry_is_emergency_closed_not_left_naked(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fail_triggers=True)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert (s.fired, s.failed, s.rejected) == (0, 1, 0)
    assert state.open_trades() == []  # not live — the row exists but is resolved
    # Ledger-first: the fill was booked, then aborted — auditable, and a crash
    # mid-protection would have left a row the resolver still manages.
    aborted = state.resolved_trades()
    assert len(aborted) == 1 and aborted[0]["status"] == "aborted"
    # entry → stop-loss (rejected) → market reduce-only flatten
    assert ex.placed[-1].order_type is OrderType.MARKET and ex.placed[-1].reduce_only
    crit = [e for e in alerter.events if e["event"] == "protection_failed"]
    assert crit and crit[0]["level"] == "critical" and crit[0]["emergency_closed"]


def test_unconfirmed_emergency_close_is_abort_failed_not_aborted(tmp_path):
    # Protection fails AND the emergency close is rejected → the position may still be live
    # and unprotected. The ledger must NOT claim a clean abort, and a human must be paged.
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fail_triggers=True, fail_close=True)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert s.failed == 1
    resolved = state.resolved_trades()
    assert len(resolved) == 1 and resolved[0]["status"] == "abort_failed"
    assert state.open_trades() == []
    events = {e["event"]: e for e in alerter.events}
    assert events["protection_failed"]["emergency_closed"] is False
    assert "emergency_close_failed" in events and events["emergency_close_failed"]["level"] == "critical"


def test_emergency_close_that_raises_is_recorded_not_propagated(tmp_path, monkeypatch):
    # A backend error during the flatten must not crash the pass — it's caught (after the
    # bounded retry), recorded as abort_failed, and alerted, so the naked position is
    # surfaced rather than lost.
    monkeypatch.setattr("hlcli.executor.protect._sleep", lambda *_: None)  # no real backoff wait
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fail_triggers=True, fail_close="raise")
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)  # must not raise

    assert s.failed == 1
    assert state.resolved_trades()[0]["status"] == "abort_failed"
    assert any(e["event"] == "emergency_close_failed" for e in alerter.events)


def test_partial_protection_cancels_the_placed_trigger_on_abort(tmp_path):
    # SL places, TP fails → flatten AND cancel the resting SL, or it ambushes the
    # next BTC position.
    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, fail_triggers="tp")
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)

    assert s.failed == 1
    sl_oid = 2  # entry was order 1, the accepted stop-loss order 2
    assert ("BTC", sl_oid) in ex.canceled
    crit = [e for e in alerter.events if e["event"] == "protection_failed"]
    assert crit and crit[0]["triggers_canceled"] == 1


# --- O-2: reconciliation runs every pass; auto-adopt behind HL_RECONCILE_ACTION ---

def _unmanaged_long(entry=100.0, size=2.0):
    from hlcli.core.types import OpenOrder, Position
    position = Position(coin="BTC", side=Side.LONG, size=size, entry_price=entry)
    stop = OpenOrder(coin="BTC", oid=7, side=Side.SHORT, size=size, price=entry - 10.0,
                     order_type="stop market", reduce_only=True, is_trigger=True)
    return position, stop


def test_unmanaged_position_alerts_even_on_a_shadow_pass(tmp_path):
    # The check must run on every non-dry pass — drift between exchange and ledger is
    # never silent just because this pass wasn't allowed to fire.
    state = StateStore(tmp_path / "state.db")
    position, _ = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(), tunable(), fire_enabled=False, alerter=alerter, now=NOW)
    assert any(e["event"] == "unmanaged_position" and e["coins"] == ["BTC"]
               for e in alerter.events)


def test_reconcile_adopt_books_stop_protected_position(tmp_path):
    # HL_RECONCILE_ACTION=adopt on a fire-enabled pass: the stop-protected orphan gets a
    # ledger row via the sentry adopt path (never inventing anything) and stops alerting.
    state = StateStore(tmp_path / "state.db")
    position, stop = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position], open_orders=[stop])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(reconcile_action="adopt"), tunable(), alerter=alerter, now=NOW)

    trade = state.open_trades()[0]
    assert trade["adopted"] == 1 and trade["sl"] == 90.0 and trade["sl_oid"] == "7"
    events = [e["event"] for e in alerter.events]
    assert "position_adopted" in events and "unmanaged_position" not in events


def test_reconcile_adopt_never_runs_on_a_shadow_pass(tmp_path):
    # Adoption writes real ledger rows — never a shadow pass's job; the alert still fires.
    state = StateStore(tmp_path / "state.db")
    position, stop = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position], open_orders=[stop])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(reconcile_action="adopt"), tunable(),
             fire_enabled=False, alerter=alerter, now=NOW)
    assert state.open_trades() == []
    assert any(e["event"] == "unmanaged_position" for e in alerter.events)


def test_reconcile_default_alert_only(tmp_path):
    # The default stays hands-off: alert, adopt nothing.
    state = StateStore(tmp_path / "state.db")
    position, stop = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position], open_orders=[stop])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(), tunable(), alerter=alerter, now=NOW)
    assert state.open_trades() == []
    assert any(e["event"] == "unmanaged_position" for e in alerter.events)


def test_paper_fire_skips_native_protection(tmp_path):
    # Paper relies on the resolver, so a paper fire places no protective triggers.
    from hlcli.exchange.paper import PaperExchange
    from hlcli.tests._helpers import FakeMarks

    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks({"BTC": 100.0}), state=state)
    state.enqueue(_cand())
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)
    assert s.fired == 1 and len(state.open_trades()) == 1


def test_shadow_row_does_not_mask_a_real_unmanaged_position(tmp_path):
    # A shadow (hypothetical) trade claims nothing on the exchange — a real orphan on
    # the same coin must still page. Only real ledger rows count as "managed".
    state = StateStore(tmp_path / "state.db")
    state.open_trade("shadow:BTC", "BTC", Side.LONG, 100.0, 90.0, 120.0, 1.0, 0.7, None, NOW,
                     shadow=True)
    position, _ = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(), tunable(), fire_enabled=False, alerter=alerter, now=NOW)
    assert any(e["event"] == "unmanaged_position" and e["coins"] == ["BTC"]
               for e in alerter.events)


def test_sentry_shaped_pass_reconciles_too(tmp_path):
    # `hl sentry once|run` = run_once(include_intake=False): adopt + alert must behave
    # exactly as on an exec-shaped pass.
    state = StateStore(tmp_path / "state.db")
    position, stop = _unmanaged_long()
    ex = FakeLiveExchange(Network.TESTNET, positions=[position], open_orders=[stop])
    alerter = CapturingAlerter()
    run_once(ex, state, caps(reconcile_action="adopt"), tunable(), include_intake=False,
             alerter=alerter, now=NOW)
    assert state.open_trades()[0]["adopted"] == 1
    assert any(e["event"] == "position_adopted" for e in alerter.events)
