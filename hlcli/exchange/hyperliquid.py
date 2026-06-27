"""Live Hyperliquid backend — testnet + mainnet (PLAN.md §3, §11).

Reads use the `Info` endpoint (no key). Writes use `Exchange`, which needs the
agent wallet — a read-only account (no key) can query but not trade. The SDK and
`eth_account` are lazy-imported per the keyless-paper rule (CLAUDE.md).
"""

from __future__ import annotations

from hlcli._lazy import require
from hlcli.core.types import (
    Network,
    OpenOrder,
    Order,
    OrderResult,
    OrderType,
    Position,
    Side,
)
from hlcli.exchange.marks import MarksFeed, api_url


class HyperliquidExchange:
    def __init__(
        self,
        network: Network,
        *,
        account_address: str,
        agent_key: str | None = None,
        marks: MarksFeed | None = None,
    ) -> None:
        self.network = network
        self._account_address = account_address
        self._agent_key = agent_key
        self._base_url = api_url(network)
        self._info = None
        self._exchange = None
        self._marks = marks or MarksFeed(self._base_url)

    # --- lazy SDK clients ---

    def _info_client(self):
        if self._info is None:
            self._info = require("hyperliquid.info").Info(self._base_url, skip_ws=True)
        return self._info

    def _exchange_client(self):
        if self._agent_key is None:
            raise PermissionError("this account is read-only (no agent key) — cannot trade.")
        if self._exchange is None:
            wallet = require("eth_account").Account.from_key(self._agent_key)
            self._exchange = require("hyperliquid.exchange").Exchange(
                wallet, self._base_url, account_address=self._account_address
            )
        return self._exchange

    # --- reads ---

    def get_marks(self) -> dict[str, float]:
        return self._marks.all_marks()

    def get_book(self, coin: str) -> dict | None:
        return self._marks.book(coin)

    def equity(self) -> float:
        state = self._info_client().user_state(self._account_address)
        return float(state["marginSummary"]["accountValue"])

    def get_positions(self) -> list[Position]:
        state = self._info_client().user_state(self._account_address)
        positions = []
        for entry in state.get("assetPositions", []):
            p = entry["position"]
            szi = float(p["szi"])
            if szi == 0:
                continue
            positions.append(
                Position(
                    coin=p["coin"],
                    side=Side.LONG if szi > 0 else Side.SHORT,
                    size=abs(szi),
                    entry_price=float(p["entryPx"]),
                    unrealized_pnl=float(p.get("unrealizedPnl", 0.0)),
                )
            )
        return positions

    def get_open_orders(self) -> list[OpenOrder]:
        raw = self._info_client().open_orders(self._account_address)
        return [
            OpenOrder(
                coin=o["coin"],
                oid=int(o["oid"]),
                side=Side.LONG if o["side"] == "B" else Side.SHORT,
                size=float(o["sz"]),
                price=float(o["limitPx"]),
                reduce_only=bool(o.get("reduceOnly", False)),
            )
            for o in raw
        ]

    # --- writes ---

    def place_order(self, order: Order) -> OrderResult:
        ex = self._exchange_client()
        is_buy = order.side is Side.LONG

        if order.order_type is OrderType.MARKET:
            resp = (
                ex.market_close(order.coin, sz=order.size)
                if order.reduce_only
                else ex.market_open(order.coin, is_buy, order.size)
            )
        elif order.order_type is OrderType.LIMIT:
            resp = ex.order(
                order.coin, is_buy, order.size, order.price,
                {"limit": {"tif": "Gtc"}}, reduce_only=order.reduce_only,
            )
        else:  # STOP_LOSS / TAKE_PROFIT — protective market trigger
            tpsl = "sl" if order.order_type is OrderType.STOP_LOSS else "tp"
            resp = ex.order(
                order.coin, is_buy, order.size, order.trigger_price,
                {"trigger": {"isMarket": True, "triggerPx": order.trigger_price, "tpsl": tpsl}},
                reduce_only=order.reduce_only,
            )
        return _parse_order_response(resp)

    def cancel(self, coin: str, oid: int) -> OrderResult:
        return _parse_simple(self._exchange_client().cancel(coin, oid), "canceled")

    def cancel_all(self, coin: str | None = None) -> int:
        ex = self._exchange_client()
        canceled = 0
        for order in self.get_open_orders():
            if coin is not None and order.coin != coin:
                continue
            if _parse_simple(ex.cancel(order.coin, order.oid), "canceled").accepted:
                canceled += 1
        return canceled

    def set_leverage(self, coin: str, leverage: int, *, cross: bool = True) -> OrderResult:
        resp = self._exchange_client().update_leverage(leverage, coin, cross)
        return _parse_simple(resp, "leverage_set")


def _parse_order_response(resp) -> OrderResult:
    if not isinstance(resp, dict) or resp.get("status") != "ok":
        return OrderResult(accepted=False, status="error", message=_err_message(resp))

    statuses = resp.get("response", {}).get("data", {}).get("statuses", [])
    if not statuses:
        return OrderResult(accepted=True, status="ok")

    status = statuses[0]
    if "error" in status:
        return OrderResult(accepted=False, status="error", message=str(status["error"]))
    if "resting" in status:
        return OrderResult(accepted=True, status="resting", order_id=str(status["resting"]["oid"]))
    if "filled" in status:
        f = status["filled"]
        return OrderResult(
            accepted=True, status="filled", order_id=str(f.get("oid")),
            message=f"{f.get('totalSz')} @ {f.get('avgPx')}",
        )
    return OrderResult(accepted=True, status="ok", message=str(status))


def _parse_simple(resp, ok_status: str) -> OrderResult:
    if isinstance(resp, dict) and resp.get("status") == "ok":
        return OrderResult(accepted=True, status=ok_status)
    return OrderResult(accepted=False, status="error", message=_err_message(resp))


def _err_message(resp) -> str:
    if isinstance(resp, dict):
        detail = resp.get("response")
        if isinstance(detail, str):
            return detail
    return str(resp)
