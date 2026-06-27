"""`hl asset` — per-coin price and order book, with `-w` live watch."""

from __future__ import annotations

import typer

from hlcli.cli.context import build_for, state_of
from hlcli.cli.output import emit, emit_rows
from hlcli.cli.watch import watch_rows

app = typer.Typer(no_args_is_help=True, help="Per-asset price/book; -w to watch.")


@app.command("price")
def price(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    watch: bool = typer.Option(False, "-w", "--watch", help="live refresh"),
) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)
    coin = coin.upper()

    def rows() -> list[dict]:
        return [{"coin": coin, "mark": exchange.get_marks().get(coin)}]

    if watch and not state.json_out:
        watch_rows(rows, title=f"{coin} price")
        return
    emit_rows(rows(), as_json=state.json_out, title="price")


@app.command("book")
def book(
    ctx: typer.Context,
    coin: str = typer.Argument(...),
    depth: int = typer.Option(5, "--depth"),
    watch: bool = typer.Option(False, "-w", "--watch", help="live refresh"),
) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)
    coin = coin.upper()

    def rows() -> list[dict]:
        return _book_rows(exchange.get_book(coin), depth)

    if watch and not state.json_out:
        watch_rows(rows, title=f"{coin} book", columns=["side", "px", "sz"])
        return
    emit_rows(rows(), as_json=state.json_out, title=f"{coin} book", columns=["side", "px", "sz"])


def _book_rows(snapshot: dict | None, depth: int) -> list[dict]:
    levels = (snapshot or {}).get("levels", [[], []])
    bids, asks = levels[0][:depth], levels[1][:depth]
    rows = [{"side": "ask", "px": float(a["px"]), "sz": float(a["sz"])} for a in reversed(asks)]
    rows += [{"side": "bid", "px": float(b["px"]), "sz": float(b["sz"])} for b in bids]
    return rows
