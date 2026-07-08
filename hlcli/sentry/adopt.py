"""Sentry 7d — adopting Mode A positions into the ledger (PLAN.md §15.5).

A position the exchange holds but the ledger doesn't know (a manual `hl trade`
order, another UI, or a crash between fill and ledger write) gets a ledger row —
entry at the *actual* average price, `initial_sl` at the exchange-side stop
trigger — and is thereafter managed identically to a Mode B trade (trail rules,
LLM manager, resolver).

The one hard rule: **adoption never invents a stop.** R math needs a stop, and
placing one would be an order the human didn't specify — so a position with no
stop trigger anywhere is skipped; the runner's edge-triggered `unmanaged_position`
alert keeps pointing at it until the human sets one. Adoption itself places no
orders: it only records what already exists.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hlcli.core.types import OpenOrder, Position, Side
from hlcli.exchange.base import Exchange
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore

# Ledger tp is NOT NULL, but a stop-only manual position has no target: park the
# tp far enough (100R) that the resolver can never close on it — the exit belongs
# to the stop, the trail rules, and the manager.
_UNBOUNDED_TP_R = 100.0


@dataclass
class AdoptSummary:
    adopted: list[dict] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)  # coin + reason


def adopt_unmanaged(
    exchange: Exchange,
    state: StateStore,
    *,
    alerter: Alerter | None = None,
    now: float | None = None,
) -> AdoptSummary:
    now = time.time() if now is None else now
    summary = AdoptSummary()
    known = {t["coin"] for t in state.open_trades(shadow=False)}
    orphans = [p for p in exchange.get_positions() if p.coin not in known]
    if not orphans:
        return summary

    orders = exchange.get_open_orders()
    for position in orphans:
        # Protective triggers close the position, so they sit on the opposite side.
        protective = [o for o in orders
                      if o.coin == position.coin and o.is_trigger and o.side is not position.side]
        stops = [o for o in protective if _is_stop(o, position)]
        if not stops:
            summary.skipped.append({"coin": position.coin, "reason": "no stop trigger"})
            continue

        # Several stops = several risk slices; anchor R at the farthest one (the
        # true worst-case risk). The nearest take-profit is the first target.
        sl = max(stops, key=lambda o: abs(o.price - position.entry_price)).price
        tps = [o for o in protective if not _is_stop(o, position)]
        tp = (min(tps, key=lambda o: abs(o.price - position.entry_price)).price
              if tps else _unbounded_tp(position, sl))

        trade_id = state.open_trade(
            f"adopted:{position.coin}:{int(now)}", position.coin, position.side,
            position.entry_price, sl, tp, position.size,
            0.0,  # no LLM verdict behind this entry — conviction is honestly zero
            None, now, adopted=True,
        )
        detail = {"entry": position.entry_price, "size": position.size, "sl": sl, "tp": tp,
                  "tp_from_trigger": bool(tps)}
        state.log_sentry(now, trade_id, position.coin, "adopted", detail)
        if alerter is not None:
            alerter.alert("position_adopted", coin=position.coin, **detail)
        summary.adopted.append({"coin": position.coin, "trade_id": trade_id, **detail})
    return summary


def _is_stop(order: OpenOrder, position: Position) -> bool:
    """Stop vs take-profit. The frontend order type ("stop market", "take profit
    market", …) is authoritative; an unlabeled trigger falls back to which side of
    the entry it sits on — and a ratcheted stop past entry with no label reads as
    a tp there, which fails safe: no stop found ⇒ no adoption, never a wrong R anchor."""
    if "stop" in order.order_type:
        return True
    if "take profit" in order.order_type or "tp" == order.order_type:
        return False
    below_entry = order.price < position.entry_price
    return below_entry if position.side is Side.LONG else not below_entry


def _unbounded_tp(position: Position, sl: float) -> float:
    risk = abs(position.entry_price - sl)
    offset = _UNBOUNDED_TP_R * risk
    return position.entry_price + offset if position.side is Side.LONG else max(position.entry_price - offset, 0.0)
