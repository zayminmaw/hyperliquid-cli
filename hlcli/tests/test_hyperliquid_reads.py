"""Live read parsing (user_state / frontend_open_orders), write rounding, and
trigger-order cleanup via injected fake Info/Exchange clients — the SDK is never hit."""

import pytest

from hlcli.core.types import Network, Order, OrderType, Side
from hlcli.exchange.hyperliquid import HyperliquidExchange


class FakeInfo:
    def user_state(self, address):
        return {
            "marginSummary": {"accountValue": "1234.5"},
            "assetPositions": [
                {"position": {"coin": "BTC", "szi": "0.5", "entryPx": "60000", "unrealizedPnl": "12.3"}},
                {"position": {"coin": "ETH", "szi": "-2", "entryPx": "1500", "unrealizedPnl": "-5"}},
                {"position": {"coin": "SOL", "szi": "0", "entryPx": "0", "unrealizedPnl": "0"}},
            ],
        }

    def frontend_open_orders(self, address):
        # The frontend view is the only one that includes trigger (SL/TP) orders.
        return [
            {"coin": "BTC", "oid": 1, "side": "B", "sz": "0.1", "limitPx": "59000", "reduceOnly": False},
            {"coin": "ETH", "oid": 2, "side": "A", "sz": "1", "limitPx": "1600"},
            {"coin": "BTC", "oid": 3, "side": "A", "sz": "0.1", "triggerPx": "55000",
             "orderType": "Stop Market", "isTrigger": True, "reduceOnly": True},
        ]


class FakeExchangeClient:
    """Records writes; accepts everything."""

    def __init__(self):
        self.orders = []
        self.canceled = []

    def market_open(self, coin, is_buy, sz, slippage=0.05, cloid=None):
        self.orders.append(("market_open", coin, is_buy, sz, slippage, cloid))
        return {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 9, "totalSz": str(sz), "avgPx": "100"}}]}}}

    def market_close(self, coin, sz=None, cloid=None):
        self.orders.append(("market_close", coin, sz, cloid))
        return {"status": "ok", "response": {"data": {"statuses": [{"filled": {"oid": 9, "totalSz": str(sz), "avgPx": "100"}}]}}}

    def order(self, coin, is_buy, sz, px, order_spec, reduce_only=False, cloid=None):
        self.orders.append(("order", coin, is_buy, sz, px, order_spec, reduce_only, cloid))
        return {"status": "ok", "response": {"data": {"statuses": [{"resting": {"oid": 10}}]}}}

    def cancel(self, coin, oid):
        self.canceled.append((coin, oid))
        return {"status": "ok"}


class FakeMeta:
    """Stands in for MarksFeed.sz_decimals."""

    def __init__(self, decimals=None):
        self._d = decimals if decimals is not None else {"BTC": 5, "ETH": 4}

    def sz_decimals(self, coin):
        return self._d.get(coin)


def _exchange(with_writes: bool = False) -> HyperliquidExchange:
    ex = HyperliquidExchange(Network.TESTNET, account_address="0xabc", max_entry_slippage_pct=0.3)
    ex._info = FakeInfo()  # bypass the lazy SDK client
    ex._marks.sz_decimals = FakeMeta().sz_decimals
    if with_writes:
        ex._agent_key = "0x" + "1" * 64
        ex._exchange = FakeExchangeClient()
    return ex


def test_equity():
    assert _exchange().equity() == 1234.5


def test_positions_skip_zero_and_map_side():
    positions = _exchange().get_positions()
    assert [p.coin for p in positions] == ["BTC", "ETH"]  # zero-size SOL dropped
    assert positions[0].side is Side.LONG and positions[0].size == 0.5
    assert positions[1].side is Side.SHORT and positions[1].size == 2.0


def test_open_orders_include_triggers():
    orders = _exchange().get_open_orders()
    assert orders[0].side is Side.LONG and orders[0].reduce_only is False
    assert orders[1].side is Side.SHORT and orders[1].price == 1600.0
    trigger = orders[2]
    assert trigger.is_trigger and trigger.reduce_only and trigger.price == 55000.0


def test_cancel_all_covers_trigger_orders():
    ex = _exchange(with_writes=True)
    assert ex.cancel_all() == 3
    assert (("BTC", 3)) in ex._exchange.canceled  # the SL trigger, not just limits


def test_writes_blocked_without_key():
    ex = _exchange()  # no agent_key
    with pytest.raises(PermissionError):
        ex._exchange_client()


# --- wire rounding: size floors to szDecimals, prices to the exchange rule ---

def test_market_size_is_floored_to_sz_decimals():
    ex = _exchange(with_writes=True)
    ex.place_order(Order(coin="BTC", side=Side.LONG, order_type=OrderType.MARKET, size=0.123456789))
    # floored size, never up; entry slippage capped at the caps value (not the SDK's 5%)
    assert ex._exchange.orders[0] == ("market_open", "BTC", True, 0.12345, 0.003, None)


def test_entry_slippage_cap_is_plumbed_from_caps():
    ex = HyperliquidExchange(Network.TESTNET, account_address="0xabc", max_entry_slippage_pct=0.15)
    ex._info = FakeInfo()
    ex._marks.sz_decimals = FakeMeta().sz_decimals
    ex._agent_key = "0x" + "1" * 64
    ex._exchange = FakeExchangeClient()
    ex.place_order(Order(coin="BTC", side=Side.LONG, order_type=OrderType.MARKET, size=1.0))
    assert ex._exchange.orders[0][4] == 0.0015  # pct → fraction on the wire


def test_trigger_price_is_rounded_for_the_wire():
    ex = _exchange(with_writes=True)
    ex.place_order(Order(
        coin="ETH", side=Side.SHORT, order_type=OrderType.STOP_LOSS,
        size=1.00009, trigger_price=1234.5678, reduce_only=True,
    ))
    kind, coin, is_buy, sz, px, spec, reduce_only, cloid = ex._exchange.orders[0]
    assert sz == 1.0  # floored at 4 decimals
    assert px == 1234.6 == spec["trigger"]["triggerPx"]  # 5 sig figs
    assert reduce_only is True


def test_size_rounding_to_zero_is_rejected_before_the_wire():
    ex = _exchange(with_writes=True)
    result = ex.place_order(Order(coin="BTC", side=Side.LONG, order_type=OrderType.MARKET, size=0.0000009))
    assert not result.accepted and "rounds to zero" in result.message
    assert ex._exchange.orders == []  # nothing reached the exchange


def test_unknown_coin_passes_through_unrounded():
    ex = _exchange(with_writes=True)
    ex.place_order(Order(coin="DOGE", side=Side.LONG, order_type=OrderType.MARKET, size=0.123456789))
    assert ex._exchange.orders[0][3] == 0.123456789  # exchange's own reject is clearer


# --- order_status_by_cloid: the transport-unknown recovery parser (D-3) ---
# Fixture shapes follow the documented orderStatus response; the testnet drill
# confirms the live shape, these lock the parse logic.

_CLOID = "0x" + "ab" * 16


def _status_exchange(response) -> HyperliquidExchange:
    ex = _exchange()

    class FakeStatusInfo(FakeInfo):
        def query_order_by_cloid(self, address, cloid):
            return response

    ex._info = FakeStatusInfo()
    return ex


def _order_status(status: str, *, orig="1.0", remaining="0.0"):
    return {"status": "order", "order": {
        "status": status,
        "order": {"oid": 77, "coin": "BTC", "limitPx": "100.3", "origSz": orig, "sz": remaining},
    }}


def test_status_by_cloid_filled():
    r = _status_exchange(_order_status("filled")).order_status_by_cloid(_CLOID)
    assert r.accepted and r.status == "filled" and r.order_id == "77"
    assert r.filled_size == 1.0 and r.avg_price == 100.3


def test_status_by_cloid_resting():
    r = _status_exchange(_order_status("open", remaining="1.0")).order_status_by_cloid(_CLOID)
    assert r.accepted and r.status == "resting" and r.filled_size == 0.0


def test_status_by_cloid_canceled_zero_fill_is_not_on_book():
    r = _status_exchange(_order_status("canceled", remaining="1.0")).order_status_by_cloid(_CLOID)
    assert r is None


def test_status_by_cloid_canceled_partial_fill_is_a_fill():
    # An IOC that filled 0.6 before the remainder canceled left a REAL position —
    # it must never read as "never on the book" (that would release the fire key).
    r = _status_exchange(_order_status("canceled", orig="1.0", remaining="0.4")).order_status_by_cloid(_CLOID)
    assert r.accepted and r.status == "filled" and r.filled_size == 0.6


def test_status_by_cloid_unknown_oid_is_none():
    assert _status_exchange({"status": "unknownOid"}).order_status_by_cloid(_CLOID) is None
    assert _status_exchange("garbage").order_status_by_cloid(_CLOID) is None
