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
    rows = []
    for p in exchange.get_positions():
        mark = marks.get(p.coin)
        # Distance to the exchange's liquidation price, in percent (wave-2 M). None when the
        # backend reports no liquidationPx (paper; a well-collateralised cross position).
        liq_dist = round(abs(mark - p.liquidation_px) / mark * 100.0, 2) \
            if p.liquidation_px is not None and mark else None
        rows.append({
            "coin": p.coin, "side": p.side.value, "size": p.size,
            "entry": p.entry_price, "mark": mark, "uPnL": p.unrealized_pnl, "liq_dist%": liq_dist,
        })
    return rows
