"""`hl agent` — the autonomous supervisor (PLAN.md §15).

One process owning all cadences: intake-directory watch (a new batch file triggers
an exec pass immediately), periodic exec and sentry passes, and the daily jobs.
The loop is deterministic code; the LLM stays boxed inside the existing decision,
management, and tuner paths. Mainnet keeps every gate it already has.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import typer

from hlcli.agent.daily import run_daily
from hlcli.agent.intake_watch import intake_dir, poll
from hlcli.agent.supervisor import (
    LAST_DAILY, LAST_EXEC, LAST_INTAKE, LAST_SENTRY, LAST_TICK,
    Cadence, Supervisor,
)
from hlcli.cli.commands.sentry import check_mainnet_graduation
from hlcli.cli.context import open_env, state_of
from hlcli.cli.output import emit, note
from hlcli.core.config_schema import load_tunable
from hlcli.executor.protect import requires_native_protection
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import network_alerter
from hlcli.safety.breaker import Breaker
from hlcli.sentry.adopt import adopt_unmanaged
from hlcli.sentry.live import manage_live
from hlcli.sentry.shadow import shadow_pass
from hlcli.state.store import StateStore
from hlcli.tuner.promote import pending_proposals

app = typer.Typer(no_args_is_help=True, help="Autonomous supervisor (agent mode).")


@app.command("run")
def run(
    ctx: typer.Context,
    with_shadow: bool = typer.Option(False, "--shadow", help="also log sentry LLM proposals vs the baseline"),
    with_manage: bool = typer.Option(False, "--manage", help="also apply gated LLM management actions"),
) -> None:
    """Run the supervisor until interrupted. Cadences come from the tunable surface
    (read at start; pass behavior reloads every pass as usual)."""
    g = state_of(ctx)
    if with_shadow and with_manage:
        raise typer.BadParameter("--shadow and --manage are exclusive: manage already logs "
                                 "every proposal, so shadowing on top doubles the LLM spend")
    if with_manage:
        check_mainnet_graduation(g)
    exchange, state, caps, tunable = open_env(g, for_write=True)
    alerter = network_alerter(caps, g.network)
    directory = intake_dir(caps, g.network)
    directory.mkdir(parents=True, exist_ok=True)
    a = tunable.agent
    cadence = Cadence(a.intake_poll_seconds, a.exec_interval_minutes * 60,
                      a.sentry_interval_seconds, caps.agent_daily_utc)

    def exec_pass() -> None:
        run_once(exchange, state, caps, load_tunable(), dry_run=g.dry_run, alerter=alerter)

    def sentry_pass() -> None:
        t = load_tunable()
        adopt_unmanaged(exchange, state, alerter=alerter)  # Mode A positions join the book first
        if with_shadow:
            # Propose BEFORE the rules mutate the book, same ordering as `sentry run`.
            shadow_pass(exchange, state, caps, t, breaker_tripped=Breaker(state, caps).tripped())
        if with_manage:
            manage_live(exchange, state, caps, t,
                        native_protected=requires_native_protection(g.network), alerter=alerter)
        run_once(exchange, state, caps, t, include_intake=False, alerter=alerter)

    supervisor = Supervisor(
        state, alerter, cadence,
        poll_intake=lambda: poll(directory, state, alerter),
        exec_pass=exec_pass, sentry_pass=sentry_pass,
        # journal yesterday (distilling the reflection lesson) → tuners →
        # paper-only auto-promote → report alert
        daily_pass=lambda: run_daily(exchange, state, caps, g.network, alerter),
    )

    mode = " (+shadow)" if with_shadow else " (+manage)" if with_manage else ""
    note(f"agent on {g.network.value}{mode}: intake {directory} every {a.intake_poll_seconds:g}s · "
         f"exec {a.exec_interval_minutes:g}m · sentry {a.sentry_interval_seconds:g}s · "
         f"daily {caps.agent_daily_utc} UTC — ctrl-c to stop")

    def on_tick(ran: list[str]) -> None:
        if ran:
            note(f"[dim]{time.strftime('%H:%M:%S')}[/dim] " + " · ".join(ran))

    def on_error(failures: int, exc: Exception) -> None:
        note(f"[yellow]{time.strftime('%H:%M:%S')} tick failed ({failures}x): {exc}[/yellow]")

    try:
        supervisor.run_forever(on_tick=on_tick, on_error=on_error)
    except KeyboardInterrupt:
        note("stopped")


@app.command("status")
def status(ctx: typer.Context) -> None:
    """The supervisor's pulse (from the state store, works while it runs elsewhere):
    pass ages, breaker, book, today's realized P&L, pending tuner proposals."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=False)
    now = time.time()
    last_tick = _age(state, LAST_TICK, now)
    positions = exchange.get_positions()
    emit(
        {
            "network": g.network.value,
            # "running" = a tick landed within 3 poll intervals; anything older means stopped/stuck
            "running": last_tick is not None and last_tick < 3 * tunable.agent.intake_poll_seconds,
            "last_tick_age_s": last_tick,
            "last_intake_age_s": _age(state, LAST_INTAKE, now),
            "last_exec_age_s": _age(state, LAST_EXEC, now),
            "last_sentry_age_s": _age(state, LAST_SENTRY, now),
            "last_daily": state.meta_get(LAST_DAILY),
            "breaker": "tripped" if Breaker(state, caps).tripped() else "clear",
            "equity": exchange.equity(),
            "open_positions": len(positions),
            "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4),
            "realized_today": _realized_today(state, now),
            "deferred": state.deferred_count(),
            "pending_proposals": pending_proposals(caps),
            "intake_dir": str(intake_dir(caps, g.network)),
        },
        as_json=g.json_out, title="agent status",
    )


def _age(state: StateStore, key: str, now: float) -> float | None:
    raw = state.meta_get(key)
    return None if raw is None else round(now - float(raw), 1)


def _realized_today(state: StateStore, now: float) -> float:
    midnight = datetime.fromtimestamp(now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    return round(sum(t["realized"] or 0.0 for t in state.resolved_trades()
                     if not t["shadow"] and (t["closed_at"] or 0) >= midnight), 4)
