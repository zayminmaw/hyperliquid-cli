"""`hl exec` — LLM executor (Mode B). Phase 2 ships the deterministic pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

import typer

from hlcli.cli.context import open_env, state_of
from hlcli.cli.output import emit, emit_rows, note
from hlcli.cli.watch import watch_rows
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.executor.intake import make_candidate, parse_batch
from hlcli.executor.monitor import position_health
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import network_alerter
from hlcli.safety.breaker import Breaker
from hlcli.safety.graduation import assess
from hlcli.state.store import open_state

app = typer.Typer(no_args_is_help=True, help="LLM executor (Mode B).")


@app.command("propose")
def propose(
    ctx: typer.Context,
    coin: Optional[str] = typer.Option(None, "--coin", "--pair"),
    entry: Optional[float] = typer.Option(None, "--entry"),
    tp: Optional[float] = typer.Option(None, "--tp"),
    sl: Optional[float] = typer.Option(None, "--sl"),
    reason: str = typer.Option("", "--reason"),
    news: str = typer.Option("", "--news"),
    file: Optional[Path] = typer.Option(None, "--file", help="JSON list/object batch"),
) -> None:
    """Queue candidate setup(s) into the intake stream for the current network."""
    g = state_of(ctx)
    state = open_state(get_caps(), g.network)

    try:
        if file is not None:
            data = json.loads(file.read_text())
            candidates = parse_batch(data if isinstance(data, list) else [data])
        elif None in (coin, entry, tp, sl):
            raise typer.BadParameter("provide --coin --entry --tp --sl, or --file <batch.json>")
        else:
            candidates = [make_candidate(coin, entry, tp, sl, reasoning=reason, news=news)]
    except ValueError as exc:  # incoherent levels
        raise typer.BadParameter(str(exc))

    enqueued = sum(state.enqueue(c) for c in candidates)
    emit(
        {"network": g.network.value, "submitted": len(candidates),
         "enqueued": enqueued, "duplicates": len(candidates) - enqueued},
        as_json=g.json_out, title="exec propose",
    )


@app.command("once")
def once(ctx: typer.Context) -> None:
    """One full executor pass (intake → enrich → LLM decision → gate → fire → log)."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=True)
    summary = run_once(exchange, state, caps, tunable, dry_run=g.dry_run, alerter=network_alerter(caps, g.network))
    emit(summary.model_dump(), as_json=g.json_out, title="exec once")


@app.command("shadow")
def shadow(ctx: typer.Context) -> None:
    """Decide + gate + log a full pass but fire nothing (pre-mainnet confidence + tuner data)."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=False)
    summary = run_once(exchange, state, caps, tunable, fire_enabled=False, dry_run=g.dry_run)
    emit(summary.model_dump(), as_json=g.json_out, title="exec shadow")


_FAILURE_ALERT_EVERY = 10  # alert on the 1st failure of a streak, then every Nth
_MAX_BACKOFF_SECONDS = 60.0


@app.command("run")
def run(ctx: typer.Context, interval: float = typer.Option(5.0, "--interval", help="seconds between passes")) -> None:
    """Continuous executor loop (ctrl-c to stop)."""
    g = state_of(ctx)
    exchange, state, caps, _ = open_env(g, for_write=True)
    alerter = network_alerter(caps, g.network)
    note(f"executor running every {interval}s on {g.network.value} — ctrl-c to stop")
    failures = 0
    try:
        while True:
            try:
                # Re-read the tunable surface each pass so a `tune promote` mid-run
                # takes effect without a restart (the decision prompt already does).
                s = run_once(exchange, state, caps, load_tunable(), dry_run=g.dry_run, alerter=alerter)
                failures = 0
                note(f"[dim]{time.strftime('%H:%M:%S')}[/dim] seen={s.seen} fired={s.fired} "
                     f"deferred={s.deferred} rechecked={s.rechecked} resolved={s.resolved} managed={s.managed} "
                     f"rejected={s.rejected} failed={s.failed} dropped={s.dropped}")
            except Exception as exc:  # keep the loop alive across transient LLM/network faults
                failures += 1
                note(f"[yellow]{time.strftime('%H:%M:%S')} pass failed ({failures}x): {exc}[/yellow]")
                if failures == 1 or failures % _FAILURE_ALERT_EVERY == 0:
                    alerter.alert("pass_failed", level="warning", consecutive=failures, error=str(exc))
            # Repeated failures back off exponentially — a hard-down API isn't retried
            # every 5 seconds forever at full LLM cost.
            time.sleep(min(interval * (2 ** min(failures, 10)), _MAX_BACKOFF_SECONDS) if failures else interval)
    except KeyboardInterrupt:
        note("stopped")


@app.command("breaker")
def breaker(
    ctx: typer.Context,
    switch: Optional[bool] = typer.Option(None, "--on/--off", help="trip or clear the kill switch"),
) -> None:
    """Show or toggle the kill switch (halts new fires; open positions still managed)."""
    g = state_of(ctx)
    caps = get_caps()
    b = Breaker(open_state(caps, g.network), caps)
    if switch is not None:
        b.set(switch)
    emit(
        {"network": g.network.value, "breaker": "tripped" if b.tripped() else "clear"},
        as_json=g.json_out, title="exec breaker",
    )


@app.command("status")
def status(ctx: typer.Context, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
    """Live position health for the executor's book."""
    g = state_of(ctx)
    exchange, state, _caps, _tunable = open_env(g, for_write=False)

    def rows() -> list[dict]:
        return position_health(exchange)

    if watch and not g.json_out:
        watch_rows(rows, title="exec status")
        return
    emit_rows(rows(), as_json=g.json_out, title="exec status")
    if not g.json_out:
        note(f"deferred (awaiting re-check): {state.deferred_count()}")


@app.command("report")
def report(ctx: typer.Context) -> None:
    """Account summary: equity, open positions, unrealized P&L, breaker + graduation readiness."""
    g = state_of(ctx)
    exchange, state, caps, _tunable = open_env(g, for_write=False)
    positions = exchange.get_positions()
    emit(
        {
            "network": g.network.value,
            "equity": exchange.equity(),
            "open_positions": len(positions),
            "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4),
            "breaker": "tripped" if Breaker(state, caps).tripped() else "clear",
            "deferred": state.deferred_count(),  # WAIT candidates parked for re-check
            "graduation": assess(state.resolved_trades(), caps),
        },
        as_json=g.json_out, title="exec report",
    )
