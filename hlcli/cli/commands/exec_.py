"""`hl exec` — LLM executor (Mode B). Phase 1 ships `once`/`status`/`report`."""

from __future__ import annotations

import typer

from hlcli.cli.context import build_for, state_of
from hlcli.cli.output import emit, emit_rows
from hlcli.cli.stubs import stub_command
from hlcli.cli.watch import watch_rows
from hlcli.executor.monitor import position_health
from hlcli.executor.runner import run_once

app = typer.Typer(no_args_is_help=True, help="LLM executor (Mode B).")

# Order-path verbs land in their phases.
for _cmd, _phase in {"propose": 2, "run": 2, "shadow": 3, "breaker": 2}.items():
    app.command(name=_cmd)(stub_command("exec", _cmd, _phase))


@app.command("once")
def once(ctx: typer.Context) -> None:
    """One full executor pass (Phase 0/1: no-op skeleton)."""
    state = state_of(ctx)
    exchange = build_for(state, for_write=True)
    summary = run_once(exchange, dry_run=state.dry_run)
    emit(summary.model_dump(), as_json=state.json_out, title="exec once")


@app.command("status")
def status(ctx: typer.Context, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
    """Live position health for the current account."""
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)

    def rows() -> list[dict]:
        return position_health(exchange)

    if watch and not state.json_out:
        watch_rows(rows, title="exec status")
        return
    emit_rows(rows(), as_json=state.json_out, title="exec status")


@app.command("report")
def report(ctx: typer.Context) -> None:
    """Account summary: equity, open positions, unrealized P&L."""
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)
    positions = exchange.get_positions()
    emit(
        {
            "network": state.network.value,
            "equity": exchange.equity(),
            "open_positions": len(positions),
            "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4),
            "breaker": "n/a until Phase 2",
        },
        as_json=state.json_out, title="exec report",
    )
