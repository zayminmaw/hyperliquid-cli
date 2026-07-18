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
from hlcli.agent.liveness import Liveness, classify, stale_after_seconds
from hlcli.agent.supervisor import (
    LAST_DAILY, LAST_EXEC, LAST_INTAKE, LAST_SENTRY, LAST_TICK,
    Cadence, Supervisor,
)
from hlcli.cli.commands.sentry import check_mainnet_graduation, require_exclusive_modes
from hlcli.cli.context import open_env, state_of
from hlcli.cli.output import emit, note
from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig, load_tunable
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
    require_exclusive_modes(with_shadow, with_manage)
    if with_manage:
        check_mainnet_graduation(g)
    exchange, state, caps, tunable = open_env(g, for_write=True)
    alerter = network_alerter(caps, g.network)
    # Reconcile-before-respawn (audit F): resuming after a stale heartbeat with positions still
    # open means they sat unmanaged during the downtime — page before resuming (the first sentry
    # tick then reconciles them). A clean restart with a fresh/empty book stays quiet.
    prior, _, _ = _liveness(state, caps, tunable, time.time())
    if prior is Liveness.STALE and (resumed := len(exchange.get_positions())):
        alerter.alert("agent_resumed_with_unmanaged", level="warning", open_positions=resumed)
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
    live, stale_after, last_tick = _liveness(state, caps, tunable, now)
    positions = exchange.get_positions()
    emit(
        {
            "network": g.network.value,
            # liveness: never (no heartbeat) | alive (tick within threshold) | stale (loop stopped/stuck)
            "liveness": live.value,
            "running": live is Liveness.ALIVE,  # back-compat: alive ⇒ running
            "stale_after_s": stale_after,
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


@app.command("watchdog")
def watchdog(ctx: typer.Context) -> None:
    """Page if the supervisor looks dead while positions are open — run from cron/systemd.

    A hard-killed loop (SIGKILL, host crash) can't alert for itself, and its open positions
    then sit unmanaged behind only their native SL/TP. This separate reader emits a critical
    `agent_stale` alert when the last tick is older than the staleness threshold AND the book
    is non-empty, and exits non-zero so a monitor can escalate. Quiet (exit 0) when the loop
    is alive, never started, or stale with nothing at risk."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=False)
    now = time.time()
    live, stale_after, last_tick = _liveness(state, caps, tunable, now)
    open_positions = len(exchange.get_positions())
    paged = live is Liveness.STALE and open_positions > 0
    if paged:
        network_alerter(caps, g.network).alert(
            "agent_stale", level="critical", last_tick_age_s=last_tick,
            stale_after_s=stale_after, open_positions=open_positions,
        )
    emit(
        {
            "network": g.network.value, "liveness": live.value, "last_tick_age_s": last_tick,
            "stale_after_s": stale_after, "open_positions": open_positions, "paged": paged,
        },
        as_json=g.json_out, title="agent watchdog",
    )
    if paged:
        raise typer.Exit(1)


def _age(state: StateStore, key: str, now: float) -> float | None:
    raw = state.meta_get(key)
    return None if raw is None else round(now - float(raw), 1)


def _liveness(state: StateStore, caps: Caps, tunable: TunableConfig, now: float) -> tuple[Liveness, float, float | None]:
    """(verdict, staleness threshold, last-tick age) for the supervisor heartbeat — the single
    place `run`/`status`/`watchdog` derive liveness from LAST_TICK (audit F)."""
    last_tick = _age(state, LAST_TICK, now)
    stale_after = stale_after_seconds(tunable.agent.intake_poll_seconds, caps.agent_stale_after_seconds)
    return classify(last_tick, stale_after), stale_after, last_tick


def _realized_today(state: StateStore, now: float) -> float:
    midnight = datetime.fromtimestamp(now, tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0).timestamp()
    return round(sum(t["realized"] or 0.0 for t in state.resolved_trades()
                     if not t["shadow"] and (t["closed_at"] or 0) >= midnight), 4)
