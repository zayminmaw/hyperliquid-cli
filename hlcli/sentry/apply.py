"""Sentry 6a apply layer — turns planned actions into ledger + exchange state.

The engine proposes; this module fires. The same safety discipline as the executor
applies: ledger writes are ordered so a crash leaves a position the resolver still
manages, scale-outs are idempotent (key recorded *before* the order, like `fire`),
and a live stop is moved place-new-then-cancel-old so the position is never naked.

Shadow trades are managed identically but orderlessly — the hypothetical book must
experience the same management the real book would, or shadow outcomes (the tuner
and graduation data) stop being comparable.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Candle, Order, OrderType, Side
from hlcli.exchange.base import Exchange
from hlcli.safety.alerts import Alerter
from hlcli.sentry.engine import MoveStop, ScaleOut, active, plan
from hlcli.state.store import StateStore

_CANDLE_INTERVAL = "15m"  # matches the executor's decision-context tail


@dataclass
class ManageSummary:
    stops_moved: int = 0
    scaled_out: int = 0
    failed: int = 0
    actions: list[dict] = field(default_factory=list)  # what happened (or would, in dry-run)


def manage_open_trades(
    exchange: Exchange,
    state: StateStore,
    tunable: TunableConfig,
    now: float,
    *,
    marks: dict[str, float] | None = None,
    native_protected: bool = False,
    shadow_only: bool = False,
    dry_run: bool = False,
    alerter: Alerter | None = None,
) -> ManageSummary:
    """Run the 6a rules over every open trade. A shadow pass (`shadow_only`) manages
    only hypothetical rows — it may hold a read-only exchange and must never place
    an order. Dry-run previews the plan without touching anything."""
    summary = ManageSummary()
    cfg = tunable.trail
    if not active(cfg):
        return summary

    marks = marks if marks is not None else exchange.get_marks()
    bars_by_coin: dict[str, list[Candle]] = {}

    for trade in state.open_trades():
        if shadow_only and not trade["shadow"]:
            continue  # a shadow pass never touches real trades
        mark = marks.get(trade["coin"])
        if mark is None:
            continue
        bars = _bars(exchange, trade["coin"], bars_by_coin) if cfg.style == "atr" else []
        for action in plan(trade, mark, bars, cfg):
            if dry_run:
                summary.actions.append(_preview(trade, action))
                continue
            if isinstance(action, ScaleOut):
                _apply_scale_out(exchange, state, trade, action, now,
                                 native_protected=native_protected, summary=summary, alerter=alerter)
            else:
                _apply_move_stop(exchange, state, trade, action, now,
                                 native_protected=native_protected, summary=summary, alerter=alerter)
    return summary


def _apply_scale_out(exchange: Exchange, state: StateStore, trade: dict, action: ScaleOut,
                     now: float, *, native_protected: bool, summary: ManageSummary,
                     alerter: Alerter | None) -> None:
    exit_price = action.level
    close_size = action.size

    if not trade["shadow"]:
        key = f"sentry:scale:{trade['id']}"
        if state.already_fired(key):
            return  # a crash between order and ledger split must not double-close
        state.record_fire(key, None, now)
        result = exchange.place_order(_partial_close(trade, close_size, exit_price, native_protected))
        filled = result.filled_size if result.filled_size is not None else close_size
        if not result.accepted or filled <= 0:
            state.release_fire(key)
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action="scale_out",
                  reason=result.message or result.status)
            return
        close_size = filled
        if native_protected and result.avg_price is not None:
            exit_price = result.avg_price  # book the real fill, not the ideal ladder level
        # The resting SL/TP triggers are now oversized, which is safe: reduce-only
        # means they can never close more than the position that remains.

    realized, r_multiple = _partial_pnl(trade, close_size, exit_price)
    state.split_trade(trade["id"], close_size, exit_price, realized, r_multiple, now)
    trade["size"] -= close_size  # the stop move that may follow guards the remainder
    trade["scaled_out"] = 1
    summary.scaled_out += 1
    detail = {"size": close_size, "level": exit_price, "r": action.r, "realized": realized}
    state.log_sentry(now, trade["id"], trade["coin"], "scale_out", detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": "scale_out", **detail})


def _apply_move_stop(exchange: Exchange, state: StateStore, trade: dict, action: MoveStop,
                     now: float, *, native_protected: bool, summary: ManageSummary,
                     alerter: Alerter | None) -> None:
    if native_protected and not trade["shadow"]:
        if not _sync_native_stop(exchange, trade, action.new_sl):
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action="move_stop",
                  reason="new trigger rejected; old stop kept")
            return

    old_sl = trade["sl"]
    state.update_trade_sl(trade["id"], action.new_sl)
    trade["sl"] = action.new_sl
    summary.stops_moved += 1
    detail = {"from": old_sl, "to": action.new_sl, "reason": action.reason}
    state.log_sentry(now, trade["id"], trade["coin"], "move_stop", detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": "move_stop", **detail})


def _sync_native_stop(exchange: Exchange, trade: dict, new_sl: float) -> bool:
    """Replace the exchange-side stop: place the tighter trigger first, cancel the old
    one only once the new one rests. If placing fails the old (wider) stop stays — the
    ledger keeps the old level too, so the two never disagree in the risky direction.
    A crash in between leaves two reduce-only stops; the tighter fires first and the
    resolver's post-close trigger cleanup removes the survivor."""
    side = Side(trade["side"])
    result = exchange.place_order(Order(
        coin=trade["coin"], side=Side.SHORT if side is Side.LONG else Side.LONG,
        order_type=OrderType.STOP_LOSS, size=trade["size"],
        trigger_price=new_sl, reduce_only=True,
    ))
    if not result.accepted:
        return False
    for order in exchange.get_open_orders():
        if (order.coin == trade["coin"] and order.is_trigger and order.reduce_only
                and "stop" in order.order_type and str(order.oid) != (result.order_id or "")):
            exchange.cancel(trade["coin"], order.oid)
    return True


def _partial_close(trade: dict, size: float, level: float, native_protected: bool) -> Order:
    """Paper: a reduce-only LIMIT at the ladder level (the book realizes it exactly,
    same contract as the resolver). Live: a reduce-only MARKET for the fraction."""
    side = Side(trade["side"])
    closing = Side.SHORT if side is Side.LONG else Side.LONG
    if native_protected:
        return Order(coin=trade["coin"], side=closing, order_type=OrderType.MARKET,
                     size=size, reduce_only=True)
    return Order(coin=trade["coin"], side=closing, order_type=OrderType.LIMIT,
                 size=size, price=level, reduce_only=True)


def _partial_pnl(trade: dict, size: float, exit_price: float) -> tuple[float, float]:
    """P&L of the closed fraction; R measured against the *initial* risk so a ratcheted
    stop can't inflate the R-multiple the tuner learns from."""
    side = Side(trade["side"])
    per_unit = (exit_price - trade["entry"]) if side is Side.LONG else (trade["entry"] - exit_price)
    risk = abs(trade["entry"] - (trade["initial_sl"] or trade["sl"]))
    realized = round(per_unit * size, 6)
    r_multiple = round(per_unit / risk, 4) if risk > 0 else 0.0
    return realized, r_multiple


def _bars(exchange: Exchange, coin: str, cache: dict[str, list[Candle]]) -> list[Candle]:
    """Candle tail for the ATR trail, once per coin per pass. Best-effort: a feed
    hiccup degrades to 'no ATR' (the trail stays put) — it must never abort managing
    the rest of the book."""
    if coin not in cache:
        try:
            cache[coin] = exchange.get_candles(coin, interval=_CANDLE_INTERVAL)
        except (httpx.HTTPError, KeyError, ValueError, TypeError):
            cache[coin] = []
    return cache[coin]


def _preview(trade: dict, action: ScaleOut | MoveStop) -> dict:
    base = {"trade_id": trade["id"], "coin": trade["coin"]}
    if isinstance(action, ScaleOut):
        return {**base, "action": "scale_out", "size": action.size, "level": action.level, "r": action.r}
    return {**base, "action": "move_stop", "from": trade["sl"], "to": action.new_sl, "reason": action.reason}


def _emit(alerter: Alerter | None, event: str, **fields) -> None:
    if alerter is not None:
        alerter.alert(event, level="warning", **fields)
