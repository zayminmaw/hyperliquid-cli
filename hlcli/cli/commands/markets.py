"""`hl markets` — list tradable markets and their marks."""

from __future__ import annotations

import typer

from hlcli.cli.context import build_for, state_of
from hlcli.cli.output import emit_rows
from hlcli.core.config import get_caps

app = typer.Typer(no_args_is_help=True, help="Market data.")


def _select(marks: dict[str, float], coins: list[str] | None, show_all: bool) -> list[str]:
    if coins:
        return [c.upper() for c in coins]
    if show_all:
        return sorted(marks)
    return [c for c in get_caps().coins if c in marks]  # allowed coins by default


@app.command("ls")
def ls(ctx: typer.Context, show_all: bool = typer.Option(False, "--all", help="every market, not just ALLOWED_COINS")) -> None:
    state = state_of(ctx)
    marks = build_for(state, for_write=False).get_marks()
    emit_rows(
        [{"coin": c, "mark": marks.get(c)} for c in _select(marks, None, show_all)],
        as_json=state.json_out, title="markets",
    )


@app.command("prices")
def prices(
    ctx: typer.Context,
    coins: list[str] = typer.Argument(None, help="coins to price (default: ALLOWED_COINS)"),
    show_all: bool = typer.Option(False, "--all"),
) -> None:
    state = state_of(ctx)
    marks = build_for(state, for_write=False).get_marks()
    emit_rows(
        [{"coin": c, "mark": marks.get(c)} for c in _select(marks, coins, show_all)],
        as_json=state.json_out, title="prices",
    )
