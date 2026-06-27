"""Position monitoring (PLAN.md §5 — monitor step).

Phase 1 is read-only: enrich open positions with the live mark and unrealized
P&L for `exec status`/watch. Automated SL/TP/expiry *action* belongs to the
executor (Phase 2) and native exchange triggers (Phase 5); this is the view those
build on.
"""

from __future__ import annotations

from hlcli.exchange.base import Exchange


def position_health(exchange: Exchange) -> list[dict]:
    marks = exchange.get_marks()
    return [
        {
            "coin": p.coin,
            "side": p.side.value,
            "size": p.size,
            "entry": p.entry_price,
            "mark": marks.get(p.coin),
            "uPnL": p.unrealized_pnl,
        }
        for p in exchange.get_positions()
    ]
