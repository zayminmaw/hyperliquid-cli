"""Phase-labelled command stubs — one place that says "this verb isn't built yet"."""

from __future__ import annotations

from collections.abc import Callable

import typer

from hlcli.cli.context import state_of
from hlcli.cli.output import emit, note


def stub_command(group: str, command: str, phase: int) -> Callable[[typer.Context], None]:
    def run(ctx: typer.Context) -> None:
        state = state_of(ctx)
        if state.json_out:
            emit({"error": "not_implemented", "command": f"{group} {command}", "phase": phase}, as_json=True)
        else:
            note(f"[yellow]`hl {group} {command}` is not built yet — arrives in Phase {phase}.[/yellow]")
        raise typer.Exit(1)

    return run


def stub_group(group: str, help_text: str, commands: dict[str, int]) -> typer.Typer:
    grp = typer.Typer(no_args_is_help=True, help=help_text)
    for command, phase in commands.items():
        grp.command(name=command)(stub_command(group, command, phase))
    return grp
