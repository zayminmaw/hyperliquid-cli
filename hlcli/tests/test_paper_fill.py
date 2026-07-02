"""Paper exchange fill simulation + persistent book + equity."""

from hlcli.core.types import Order, OrderType, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks


def _ex(tmp_path, marks=None):
    state = StateStore(tmp_path / "state.db")
    return PaperExchange(10_000.0, marks=FakeMarks(marks), state=state), state


def _order(coin="BTC", side=Side.LONG, size=2.0, price=100.0, reduce_only=False):
    return Order(coin=coin, side=side, order_type=OrderType.LIMIT, size=size, price=price, reduce_only=reduce_only)


def test_open_long_persists_position(tmp_path):
    ex, _ = _ex(tmp_path)
    result = ex.place_order(_order())
    assert result.accepted and result.status == "filled"
    pos = ex.get_positions()
    assert len(pos) == 1 and pos[0].side is Side.LONG and pos[0].size == 2.0


def test_unrealized_pnl_tracks_mark(tmp_path):
    ex, _ = _ex(tmp_path, marks={"BTC": 110.0})
    ex.place_order(_order(price=100.0))  # long 2 @ 100, mark 110 -> +20
    assert ex.get_positions()[0].unrealized_pnl == 20.0
    assert ex.equity() == 10_020.0


def test_closing_realizes_pnl(tmp_path):
    ex, state = _ex(tmp_path, marks={"BTC": 100.0})
    ex.place_order(_order(side=Side.LONG, size=2.0, price=100.0))
    ex.place_order(_order(side=Side.SHORT, size=2.0, price=110.0))  # close at 110 -> +20
    assert ex.get_positions() == []
    assert state.paper_realized() == 20.0
    assert ex.equity() == 10_020.0


def test_partial_close_keeps_remainder(tmp_path):
    ex, _ = _ex(tmp_path, marks={"BTC": 100.0})
    ex.place_order(_order(side=Side.LONG, size=3.0, price=100.0))
    ex.place_order(_order(side=Side.SHORT, size=1.0, price=100.0))
    pos = ex.get_positions()
    assert len(pos) == 1 and pos[0].size == 2.0


def test_increase_averages_entry(tmp_path):
    ex, _ = _ex(tmp_path)
    ex.place_order(_order(size=1.0, price=100.0))
    ex.place_order(_order(size=1.0, price=120.0))
    assert ex.get_positions()[0].entry_price == 110.0


def test_oversized_opposite_order_flips_the_position(tmp_path):
    ex, _ = _ex(tmp_path)
    ex.place_order(_order(side=Side.LONG, size=2.0, price=100.0))
    ex.place_order(_order(side=Side.SHORT, size=5.0, price=100.0))  # closes 2, flips 3 short
    pos = ex.get_positions()[0]
    assert pos.side is Side.SHORT and pos.size == 3.0 and pos.entry_price == 100.0


def test_reduce_only_never_flips(tmp_path):
    ex, _ = _ex(tmp_path)
    ex.place_order(_order(side=Side.LONG, size=2.0, price=100.0))
    ex.place_order(_order(side=Side.SHORT, size=5.0, price=100.0, reduce_only=True))
    assert ex.get_positions() == []  # closed, nothing opened the other way


def test_trigger_orders_are_rejected_on_the_paper_book(tmp_path):
    ex, _ = _ex(tmp_path)
    result = ex.place_order(Order(
        coin="BTC", side=Side.SHORT, order_type=OrderType.STOP_LOSS,
        size=1.0, trigger_price=90.0, reduce_only=True,
    ))
    assert not result.accepted  # paper protection is the resolver, not a fake instant fill


def test_book_survives_new_exchange_instance(tmp_path):
    ex, state = _ex(tmp_path)
    ex.place_order(_order())
    reopened = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
    assert len(reopened.get_positions()) == 1  # persisted across instances
