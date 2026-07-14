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
from hlcli.exchange.rounding import round_price, round_size


class HyperliquidExchange:
    def __init__(
        self,
        network: Network,
        *,
        account_address: str,
        agent_key: str | None = None,
        marks: MarksFeed | None = None,
        max_entry_slippage_pct: float = 0.3,
    ) -> None:
        self.network = network
        self._account_address = account_address
        self._agent_key = agent_key
        self._base_url = api_url(network)
        self._info = None
        self._exchange = None
        self._marks = marks or MarksFeed(self._base_url)
        # Entry slippage cap as a fraction (audit X-1): the SDK turns a market open into
        # an IOC limit at mid × (1 ± slippage), so this bounds the worst entry fill. The
        # SDK's own default is 5% — far too wide for a leveraged entry. Closes are left
        # at the SDK default on purpose: a flatten must fill.
        self._entry_slippage = max_entry_slippage_pct / 100.0

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

    def get_candles(self, coin: str, *, interval: str = "15m", lookback: int = 48):
        return self._marks.candles(coin, interval=interval, lookback=lookback)

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
        # frontend_open_orders, not open_orders: only the frontend view includes the
        # resting SL/TP trigger orders — without them cancel-all and the resolver's
        # trigger cleanup would silently miss native protection.
        raw = self._info_client().frontend_open_orders(self._account_address)
        return [
            OpenOrder(
                coin=o["coin"],
                oid=int(o["oid"]),
                side=Side.LONG if o["side"] == "B" else Side.SHORT,
                size=float(o["sz"]),
                price=float(o.get("limitPx") or o.get("triggerPx") or 0.0),
                order_type=str(o.get("orderType", "limit")).lower(),
                reduce_only=bool(o.get("reduceOnly", False)),
                is_trigger=bool(o.get("isTrigger", False)),
            )
            for o in raw
        ]

    # --- writes ---

    def place_order(self, order: Order) -> OrderResult:
        ex = self._exchange_client()
        order = self._round_for_wire(order)
        if order.size <= 0:
            return OrderResult(
                accepted=False, status="error",
                message=f"size rounds to zero at {order.coin}'s size precision",
            )
        is_buy = order.side is Side.LONG
        cloid = require("hyperliquid.utils.types").Cloid.from_str(order.cloid) if order.cloid else None

        if order.order_type is OrderType.MARKET:
            # Entries are slippage-capped IOC limits (X-1): a non-fill is a clean no-op
            # the caller retries later, never a fill worse than the cap. Reduce-only
            # closes keep the SDK's wide default — a flatten must fill.
            resp = (
                ex.market_close(order.coin, sz=order.size, cloid=cloid)
                if order.reduce_only
                else ex.market_open(order.coin, is_buy, order.size,
                                    slippage=self._entry_slippage, cloid=cloid)
            )
        elif order.order_type is OrderType.LIMIT:
            resp = ex.order(
                order.coin, is_buy, order.size, order.price,
                {"limit": {"tif": "Gtc"}}, reduce_only=order.reduce_only, cloid=cloid,
            )
        else:  # STOP_LOSS / TAKE_PROFIT — protective market trigger
            tpsl = "sl" if order.order_type is OrderType.STOP_LOSS else "tp"
            resp = ex.order(
                order.coin, is_buy, order.size, order.trigger_price,
                {"trigger": {"isMarket": True, "triggerPx": order.trigger_price, "tpsl": tpsl}},
                reduce_only=order.reduce_only, cloid=cloid,
            )
        return _parse_order_response(resp)

    def order_status_by_cloid(self, cloid: str) -> OrderResult | None:
        """Resolve a transport-unknown submit by its client order id. Returns a fill/resting
        result when the exchange has the order, or None when it never saw it (safe to skip).
        Keyless read (Info endpoint). NOTE: the orderStatus response shape is parsed
        best-effort here and must be confirmed on a testnet drill (see the D-3 plan)."""
        types = require("hyperliquid.utils.types")
        raw = self._info_client().query_order_by_cloid(self._account_address, types.Cloid.from_str(cloid))
        if not isinstance(raw, dict) or raw.get("status") != "order":
            return None  # unknownOid / unexpected → the exchange never booked this order
        inner = raw.get("order", {}) or {}
        o = inner.get("order", {}) or {}
        status = inner.get("status")
        oid = str(o["oid"]) if o.get("oid") is not None else None
        if status == "filled":
            return OrderResult(
                accepted=True, status="filled", order_id=oid,
                filled_size=_as_float(o.get("origSz") or o.get("sz")),
                avg_price=_as_float(o.get("limitPx")),
            )
        if status in ("open", "resting"):
            return OrderResult(accepted=True, status="resting", order_id=oid, filled_size=0.0)
        return None  # canceled / rejected / margin-canceled → treat as not on the book

    def _round_for_wire(self, order: Order) -> Order:
        """Round size/prices to the asset's exchange precision (size DOWN — never past
        a cap). An unknown coin passes through untouched; the exchange rejects it with
        its own message, which is clearer than us inventing precision."""
        sz_decimals = self._marks.sz_decimals(order.coin)
        if sz_decimals is None:
            return order
        return order.model_copy(update={
            "size": round_size(order.size, sz_decimals),
            "price": round_price(order.price, sz_decimals) if order.price is not None else None,
            "trigger_price": round_price(order.trigger_price, sz_decimals)
            if order.trigger_price is not None else None,
        })

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
        # Accepted but not filled — a live GTC limit can rest. filled_size=0 so the
        # executor treats it as "no position yet", never a phantom open.
        return OrderResult(
            accepted=True, status="resting", order_id=str(status["resting"]["oid"]), filled_size=0.0,
        )
    if "filled" in status:
        f = status["filled"]
        return OrderResult(
            accepted=True, status="filled", order_id=str(f.get("oid")),
            message=f"{f.get('totalSz')} @ {f.get('avgPx')}",
            filled_size=_as_float(f.get("totalSz")), avg_price=_as_float(f.get("avgPx")),
        )
    return OrderResult(accepted=True, status="ok", message=str(status))


def _as_float(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
