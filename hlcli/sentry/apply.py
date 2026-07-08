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
from hlcli.executor.protect import cancel_coin_triggers, cancel_placed, cancel_trade_triggers
from hlcli.executor.regime import DECISION_INTERVAL
from hlcli.executor.rmath import initial_risk
from hlcli.safety.alerts import Alerter
from hlcli.sentry.engine import MoveStop, ScaleOut, active, plan
from hlcli.state.store import StateStore


@dataclass
class ManageSummary:
    stops_moved: int = 0
    scaled_out: int = 0
    closed: int = 0     # 6c judgment closes
    tps_moved: int = 0  # 6c target extensions
    added: int = 0      # 6d pyramids
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
                apply_scale_out(exchange, state, trade, action, now,
                                native_protected=native_protected, summary=summary, alerter=alerter)
            else:
                apply_move_stop(exchange, state, trade, action, now,
                                native_protected=native_protected, summary=summary, alerter=alerter)
    return summary


def apply_scale_out(exchange: Exchange, state: StateStore, trade: dict, action: ScaleOut,
                    now: float, *, native_protected: bool, summary: ManageSummary,
                    alerter: Alerter | None, log_action: str = "scale_out",
                    extra: dict | None = None) -> None:
    """Bank a fraction. `log_action`/`extra` let the 6c live pass write `managed_*`
    audit rows; the idempotency key and `scaled_out` flag are shared with the rule
    ladder — one partial per trade, whoever banks it."""
    exit_price = action.level
    close_size = action.size

    if not trade["shadow"]:
        key = f"sentry:scale:{trade['id']}"
        if not state.record_fire(key, None, now):
            return  # a crash between order and ledger split must not double-close
        result = exchange.place_order(_partial_close(trade, close_size, exit_price, native_protected))
        filled = result.filled_size if result.filled_size is not None else close_size
        if not result.accepted or filled <= 0:
            state.release_fire(key)
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action=log_action,
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
    detail = {"size": close_size, "level": exit_price, "r": action.r, "realized": realized,
              **(extra or {})}
    state.log_sentry(now, trade["id"], trade["coin"], log_action, detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": log_action, **detail})


def apply_move_stop(exchange: Exchange, state: StateStore, trade: dict, action: MoveStop,
                    now: float, *, native_protected: bool, summary: ManageSummary,
                    alerter: Alerter | None, log_action: str = "move_stop",
                    extra: dict | None = None) -> bool:
    """Returns True when the stop actually moved — `apply_add` aborts on False."""
    if native_protected and not trade["shadow"]:
        ok, new_oid = _sync_native_stop(exchange, trade, action.new_sl)
        if not ok:
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action=log_action,
                  reason="new trigger rejected; old stop kept")
            return False
        if new_oid:
            state.update_trade_triggers(trade["id"], sl_oid=new_oid)
            trade["sl_oid"] = new_oid

    old_sl = trade["sl"]
    state.update_trade_sl(trade["id"], action.new_sl)
    trade["sl"] = action.new_sl
    summary.stops_moved += 1
    detail = {"from": old_sl, "to": action.new_sl, "reason": action.reason, **(extra or {})}
    state.log_sentry(now, trade["id"], trade["coin"], log_action, detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": log_action, **detail})
    return True


def apply_close(exchange: Exchange, state: StateStore, trade: dict, level: float,
                now: float, *, native_protected: bool, summary: ManageSummary,
                alerter: Alerter | None, log_action: str = "managed_close",
                extra: dict | None = None) -> None:
    """Flatten the whole position by judgment (6c CLOSE). The outcome is booked by
    the *sign* of the realized P&L — a deliberate exit in profit is a win, in loss a
    loss — because this close resolves the entry decision, unlike an external
    `closed`. Terminal, so the idempotency key is one-shot per trade."""
    exit_price = level

    if not trade["shadow"]:
        key = f"sentry:close:{trade['id']}"
        if not state.record_fire(key, None, now):
            return
        result = exchange.place_order(_partial_close(trade, trade["size"], level, native_protected))
        if not result.accepted:
            state.release_fire(key)
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action=log_action,
                  reason=result.message or result.status)
            return
        if native_protected and result.avg_price is not None:
            exit_price = result.avg_price

    realized, r_multiple = _partial_pnl(trade, trade["size"], exit_price)
    status = "won" if realized > 0 else "lost" if realized < 0 else "closed"
    state.resolve_trade(trade["id"], status, exit_price, realized, r_multiple, now)
    if native_protected and not trade["shadow"]:
        _cancel_after_close(exchange, state, trade)  # this row's triggers; siblings kept
    summary.closed += 1
    detail = {"size": trade["size"], "exit": exit_price, "realized": realized,
              "status": status, **(extra or {})}
    state.log_sentry(now, trade["id"], trade["coin"], log_action, detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": log_action, **detail})


def apply_move_tp(exchange: Exchange, state: StateStore, trade: dict, new_tp: float,
                  now: float, *, native_protected: bool, summary: ManageSummary,
                  alerter: Alerter | None, log_action: str = "managed_extend_tp",
                  extra: dict | None = None) -> None:
    """Extend the target: place-new-then-cancel-old, like the stop. A crash in
    between leaves two reduce-only take-profits — the *nearer* old one fires first,
    which is the conservative side of the mistake."""
    if native_protected and not trade["shadow"]:
        result = exchange.place_order(Order(
            coin=trade["coin"], side=_closing(trade), order_type=OrderType.TAKE_PROFIT,
            size=trade["size"], trigger_price=new_tp, reduce_only=True,
        ))
        if not result.accepted:
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action=log_action,
                  reason="new take-profit rejected; old target kept")
            return
        _cancel_old_trigger(exchange, trade, "take", trade.get("tp_oid"), result.order_id)
        if result.order_id:
            state.update_trade_triggers(trade["id"], tp_oid=result.order_id)
            trade["tp_oid"] = result.order_id

    old_tp = trade["tp"]
    state.update_trade_tp(trade["id"], new_tp)
    trade["tp"] = new_tp
    summary.tps_moved += 1
    detail = {"from": old_tp, "to": new_tp, **(extra or {})}
    state.log_sentry(now, trade["id"], trade["coin"], log_action, detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": log_action, **detail})


def apply_add(exchange: Exchange, state: StateStore, trade: dict, size: float, new_stop: float,
              mark: float, now: float, *, native_protected: bool, summary: ManageSummary,
              alerter: Alerter | None, conviction: float = 0.0, regime: str | None = None,
              log_action: str = "managed_add", extra: dict | None = None) -> None:
    """Pyramid (6d): raise the whole position's stop FIRST — no new size before the
    old risk shrinks — then fire the add, ledger the new slice as its own trades row
    (entry at the fill, initial_sl at the raised stop, so its R math is honest), and
    on a live network protect the slice with its own reduce-only pair. A slice whose
    protection fails is emergency-closed and booked `aborted`, exactly like a failed
    entry. The idempotency key derives from the coin's lifetime add ordinal, so a
    crash between order and ledger row cannot double-add."""
    side = Side(trade["side"])
    if not apply_move_stop(exchange, state, trade, MoveStop(new_sl=new_stop, reason="add"), now,
                           native_protected=native_protected, summary=summary, alerter=alerter):
        return  # stop refused to move ⇒ there is no add

    # Key on the managed row's id (globally unique, never reused) + the count of adds
    # already logged against it, so a fresh position in the same coin is never blocked
    # by a stale key, and the ordinal advances only when an add actually lands.
    ordinal = state.sentry_count_since(0.0, (log_action,), trade_id=trade["id"])
    key = f"sentry:add:{trade['id']}:{ordinal}"
    if not state.record_fire(key, None, now):
        # A consumed-but-unlogged key means a crash landed between claim and ledger last
        # time; erring safe (never double-add) leaves this row's adds parked — surface it.
        _emit(alerter, "sentry_add_skipped", coin=trade["coin"], action=log_action,
              reason="idempotent skip (prior add attempt unresolved)")
        return
    result = exchange.place_order(Order(
        coin=trade["coin"], side=side, order_type=OrderType.MARKET, size=size,
    ))
    filled = result.filled_size if result.filled_size is not None else size
    if not result.accepted or filled <= 0:
        state.release_fire(key)
        summary.failed += 1
        _emit(alerter, "sentry_failed", coin=trade["coin"], action=log_action,
              reason=result.message or result.status)
        return
    entry = result.avg_price if result.avg_price is not None else mark

    child_id = state.open_trade(trade["candidate_id"], trade["coin"], side, entry, new_stop,
                                trade["tp"], filled, conviction, regime, now)
    if native_protected and not _protect_slice(exchange, state, trade, child_id, entry,
                                               new_stop, filled, now, summary, alerter):
        return

    summary.added += 1
    detail = {"size": filled, "entry": entry, "new_stop": new_stop, "child_trade_id": child_id,
              **(extra or {})}
    state.log_sentry(now, trade["id"], trade["coin"], log_action, detail)
    summary.actions.append({"trade_id": trade["id"], "coin": trade["coin"],
                            "action": log_action, **detail})


def _protect_slice(exchange: Exchange, state: StateStore, trade: dict, child_id: int,
                   entry: float, new_stop: float, filled: float, now: float,
                   summary: ManageSummary, alerter: Alerter | None) -> bool:
    """Native SL/TP for an added slice — the same hard prerequisite as an entry:
    unprotectable ⇒ flattened, never left naked. On success the slice's own trigger
    oids are recorded so a later cancel touches this slice, never the parent's."""
    closing = _closing(trade)
    placed = []
    for order_type, trigger in ((OrderType.STOP_LOSS, new_stop), (OrderType.TAKE_PROFIT, trade["tp"])):
        result = exchange.place_order(Order(coin=trade["coin"], side=closing, order_type=order_type,
                                            size=filled, trigger_price=trigger, reduce_only=True))
        placed.append(result)
        if not result.accepted:
            close = exchange.place_order(Order(coin=trade["coin"], side=closing,
                                               order_type=OrderType.MARKET, size=filled,
                                               reduce_only=True))
            canceled = cancel_placed(exchange, trade["coin"], placed)
            exit_price = close.avg_price if close.avg_price is not None else entry
            per_unit = (exit_price - entry) if Side(trade["side"]) is Side.LONG else (entry - exit_price)
            risk = abs(entry - new_stop)
            state.resolve_trade(child_id, "aborted", exit_price, round(per_unit * filled, 6),
                                round(per_unit / risk, 4) if risk > 0 else 0.0, now)
            summary.failed += 1
            _emit(alerter, "sentry_failed", coin=trade["coin"], action="managed_add",
                  reason=f"slice protection rejected; emergency-closed (triggers_canceled={canceled})")
            return False
    state.update_trade_triggers(child_id, sl_oid=placed[0].order_id, tp_oid=placed[1].order_id)
    return True


def _closing(trade: dict) -> Side:
    return Side.SHORT if Side(trade["side"]) is Side.LONG else Side.LONG


def _sync_native_stop(exchange: Exchange, trade: dict, new_sl: float) -> tuple[bool, str | None]:
    """Replace the exchange-side stop: place the tighter trigger first, cancel the old
    one only once the new one rests. Returns (accepted, new_oid). If placing fails the
    old (wider) stop stays — the ledger keeps the old level too, so the two never
    disagree in the risky direction. Cancels the old stop by its recorded oid so a
    sibling slice's stop survives; falls back to the type match for pre-identity rows."""
    side = Side(trade["side"])
    result = exchange.place_order(Order(
        coin=trade["coin"], side=Side.SHORT if side is Side.LONG else Side.LONG,
        order_type=OrderType.STOP_LOSS, size=trade["size"],
        trigger_price=new_sl, reduce_only=True,
    ))
    if not result.accepted:
        return False, None
    _cancel_old_trigger(exchange, trade, "stop", trade.get("sl_oid"), result.order_id)
    return True, result.order_id


def _cancel_old_trigger(exchange: Exchange, trade: dict, kind: str, old_oid: str | None,
                        new_oid: str | None) -> None:
    """Cancel the stop/take-profit trigger being replaced. Prefer the row's recorded
    oid (slice-scoped — never a sibling's); fall back to the type match for legacy
    rows with no oid, which by construction have no sibling to strip."""
    if old_oid and str(old_oid).isdigit():
        exchange.cancel(trade["coin"], int(old_oid))
        return
    for order in exchange.get_open_orders():
        if (order.coin == trade["coin"] and order.is_trigger and order.reduce_only
                and kind in order.order_type and str(order.oid) != (new_oid or "")):
            exchange.cancel(trade["coin"], order.oid)


def _cancel_after_close(exchange: Exchange, state: StateStore, trade: dict) -> None:
    """Drop a closed row's SL/TP triggers by oid (siblings kept); sweep the coin only
    when no open ledger row remains — mirrors the resolver's post-close cleanup."""
    cancel_trade_triggers(exchange, trade)
    if not any(t["coin"] == trade["coin"] for t in state.open_trades(shadow=False)):
        cancel_coin_triggers(exchange, trade["coin"])


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
    risk = initial_risk(trade)
    realized = round(per_unit * size, 6)
    r_multiple = round(per_unit / risk, 4) if risk > 0 else 0.0
    return realized, r_multiple


def _bars(exchange: Exchange, coin: str, cache: dict[str, list[Candle]]) -> list[Candle]:
    """Candle tail for the ATR trail, once per coin per pass. Best-effort: a feed
    hiccup degrades to 'no ATR' (the trail stays put) — it must never abort managing
    the rest of the book."""
    if coin not in cache:
        try:
            cache[coin] = exchange.get_candles(coin, interval=DECISION_INTERVAL)
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
