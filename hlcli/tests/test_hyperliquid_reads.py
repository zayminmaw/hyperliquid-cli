"""Live read parsing (user_state / open_orders) via an injected fake Info client."""

from hlcli.core.types import Network, Side
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

    def open_orders(self, address):
        return [
            {"coin": "BTC", "oid": 1, "side": "B", "sz": "0.1", "limitPx": "59000", "reduceOnly": False},
            {"coin": "ETH", "oid": 2, "side": "A", "sz": "1", "limitPx": "1600"},
        ]


def _exchange() -> HyperliquidExchange:
    ex = HyperliquidExchange(Network.TESTNET, account_address="0xabc")
    ex._info = FakeInfo()  # bypass the lazy SDK client
    return ex


def test_equity():
    assert _exchange().equity() == 1234.5


def test_positions_skip_zero_and_map_side():
    positions = _exchange().get_positions()
    assert [p.coin for p in positions] == ["BTC", "ETH"]  # zero-size SOL dropped
    assert positions[0].side is Side.LONG and positions[0].size == 0.5
    assert positions[1].side is Side.SHORT and positions[1].size == 2.0


def test_open_orders_map_side_and_reduce_only():
    orders = _exchange().get_open_orders()
    assert orders[0].side is Side.LONG and orders[0].reduce_only is False
    assert orders[1].side is Side.SHORT and orders[1].price == 1600.0


def test_writes_blocked_without_key():
    import pytest

    ex = _exchange()  # no agent_key
    with pytest.raises(PermissionError):
        ex._exchange_client()
