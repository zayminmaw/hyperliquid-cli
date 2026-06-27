"""Live watch (`-w`) rendering.

Phase 1 implements watch as a short-interval poll rendered with `rich.Live` — it
gives the live-table UX (positions/orders/book) without managing a websocket
connection. Native websocket subscriptions (SDK `Info.subscribe`) are a clean
later refinement; the call sites won't change.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from rich import box
from rich.console import Console
from rich.live import Live
from rich.table import Table

_console = Console()


def watch_rows(
    fetch: Callable[[], list[dict]],
    *,
    title: str,
    columns: list[str] | None = None,
    interval: float = 1.0,
) -> None:
    """Re-render the table returned by `fetch()` every `interval` seconds until ctrl-c."""

    def build() -> Table:
        rows = fetch()
        table = Table(title=f"{title} — watching (ctrl-c to stop)", box=box.SIMPLE)
        cols = columns or (list(rows[0].keys()) if rows else ["(empty)"])
        for col in cols:
            table.add_column(col)
        for row in rows:
            table.add_row(*(str(row.get(col, "")) for col in cols))
        return table

    with Live(build(), refresh_per_second=4, console=_console) as live:
        try:
            while True:
                time.sleep(interval)
                live.update(build())
        except KeyboardInterrupt:
            pass
