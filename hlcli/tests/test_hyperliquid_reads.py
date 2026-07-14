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

    def market_open(self, coin, is_buy, sz, cloid=None):
        self.orders.append(("market_open", coin, is_buy, sz, cloid))
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
    ex = HyperliquidExchange(Network.TESTNET, account_address="0xabc")
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
    assert ex._exchange.orders[0] == ("market_open", "BTC", True, 0.12345, None)  # floored, never up


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
