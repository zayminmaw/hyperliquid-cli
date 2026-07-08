"""Mode A adoption (PLAN.md §15.5): a stop trigger earns a ledger row at the real
entry with an honest R anchor; no stop means skip — a stop is never invented."""

from __future__ import annotations

import io

from hlcli.core.types import OpenOrder, Position, Side
from hlcli.safety.alerts import Alerter
from hlcli.sentry.adopt import adopt_unmanaged
from hlcli.state.store import StateStore

NOW = 1_783_500_000.0


class FakeExchange:
    def __init__(self, positions=(), orders=()):
        self._positions = list(positions)
        self._orders = list(orders)

    def get_positions(self):
        return self._positions

    def get_open_orders(self):
        return self._orders


def long_btc(entry=100.0, size=2.0) -> Position:
    return Position(coin="BTC", side=Side.LONG, size=size, entry_price=entry)


def trigger(price, *, coin="BTC", side=Side.SHORT, order_type="stop market", oid=1) -> OpenOrder:
    return OpenOrder(coin=coin, oid=oid, side=side, size=2.0, price=price,
                     order_type=order_type, reduce_only=True, is_trigger=True)


def test_stop_trigger_earns_adoption(tmp_path):
    state = StateStore(tmp_path / "s.db")
    stream = io.StringIO()
    ex = FakeExchange([long_btc()], [trigger(95.0), trigger(110.0, order_type="take profit market", oid=2)])

    s = adopt_unmanaged(ex, state, alerter=Alerter(stream=stream), now=NOW)

    assert [a["coin"] for a in s.adopted] == ["BTC"] and not s.skipped
    t = state.open_trades()[0]
    assert (t["entry"], t["sl"], t["initial_sl"], t["tp"], t["size"]) == (100.0, 95.0, 95.0, 110.0, 2.0)
    assert t["adopted"] == 1 and t["shadow"] == 0 and t["conviction"] == 0.0
    assert state.sentry_for_trade(t["id"])[0]["action"] == "adopted"
    assert "position_adopted" in stream.getvalue()


def test_stopless_position_is_skipped_never_invented(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [trigger(110.0, order_type="take profit market")])

    s = adopt_unmanaged(ex, state, now=NOW)

    assert s.skipped == [{"coin": "BTC", "reason": "no stop trigger"}]
    assert state.open_trades() == []


def test_no_tp_trigger_parks_the_target_out_of_reach(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [trigger(95.0)])

    adopt_unmanaged(ex, state, now=NOW)

    t = state.open_trades()[0]
    assert t["tp"] == 100.0 + 100.0 * 5.0  # entry + 100R — the resolver can never hit it


def test_ratcheted_stop_past_entry_still_reads_as_a_stop(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [trigger(101.0, order_type="stop market")])

    s = adopt_unmanaged(ex, state, now=NOW)

    assert s.adopted and state.open_trades()[0]["sl"] == 101.0


def test_unlabeled_trigger_falls_back_to_entry_side(tmp_path):
    state = StateStore(tmp_path / "s.db")
    # below entry on a long ⇒ stop; above entry unlabeled ⇒ reads as tp (fails safe)
    ex = FakeExchange([long_btc()], [trigger(95.0, order_type="trigger")])
    assert adopt_unmanaged(ex, state, now=NOW).adopted

    state2 = StateStore(tmp_path / "s2.db")
    ex2 = FakeExchange([long_btc()], [trigger(101.0, order_type="trigger")])
    s = adopt_unmanaged(ex2, state2, now=NOW)
    assert s.skipped and not state2.open_trades()


def test_same_side_and_foreign_coin_triggers_are_ignored(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [
        trigger(95.0, side=Side.LONG),          # same side — not protective
        trigger(95.0, coin="ETH", oid=2),       # someone else's stop
    ])
    s = adopt_unmanaged(ex, state, now=NOW)
    assert s.skipped == [{"coin": "BTC", "reason": "no stop trigger"}]


def test_farthest_stop_anchors_r_nearest_tp_is_the_target(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [
        trigger(97.0), trigger(94.0, oid=2),
        trigger(108.0, order_type="take profit market", oid=3),
        trigger(120.0, order_type="take profit market", oid=4),
    ])
    adopt_unmanaged(ex, state, now=NOW)
    t = state.open_trades()[0]
    assert (t["sl"], t["tp"]) == (94.0, 108.0)
    # The anchor stop's oid is recorded so sentry manages this row's stop alone.
    assert (t["sl_oid"], t["tp_oid"]) == ("2", "3")


def test_worst_case_stop_is_the_losing_side_not_the_farthest_by_distance(tmp_path):
    # A long whose stop has ratcheted above entry (105) still holds a slice stop below
    # (98): abs-distance would wrongly anchor R at 105 (profit side); the worst case is
    # the lowest stop.
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc(entry=100.0)], [
        trigger(105.0, order_type="stop market"), trigger(98.0, order_type="stop market", oid=2),
    ])
    adopt_unmanaged(ex, state, now=NOW)
    t = state.open_trades()[0]
    assert t["sl"] == 98.0 and t["sl_oid"] == "2"


def test_adoption_is_idempotent_and_leaves_known_coins_alone(tmp_path):
    state = StateStore(tmp_path / "s.db")
    ex = FakeExchange([long_btc()], [trigger(95.0)])
    assert adopt_unmanaged(ex, state, now=NOW).adopted

    again = adopt_unmanaged(ex, state, now=NOW + 60)
    assert not again.adopted and not again.skipped
    assert len(state.open_trades()) == 1


def test_short_position_adopts_with_mirrored_levels(tmp_path):
    state = StateStore(tmp_path / "s.db")
    short = Position(coin="ETH", side=Side.SHORT, size=1.0, entry_price=1500.0)
    ex = FakeExchange([short], [trigger(1560.0, coin="ETH", side=Side.LONG)])

    adopt_unmanaged(ex, state, now=NOW)

    t = state.open_trades()[0]
    assert t["sl"] == 1560.0
    assert t["tp"] == 0.0  # 100R below entry would be negative — floored at zero
