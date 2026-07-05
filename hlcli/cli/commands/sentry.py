"""`hl sentry` — the in-trade manager (PLAN.md §14). Phase 6a: deterministic rules.

Manages what already exists: ratchets stops, banks scale-outs. It never opens a
position and never resolves one — entries and close-outs stay with `hl exec`,
which also runs these same rules inside every pass. This surface exists to run
the manager standalone (its own cadence) and to inspect what it has done.
"""

from __future__ import annotations

import time

import typer

from hlcli.cli.context import GlobalState, build_for, state_of
from hlcli.cli.output import emit, emit_rows, note
from hlcli.core.config import Caps, get_caps
from hlcli.core.config_schema import TunableConfig, load_tunable
from hlcli.core.types import Network, Side
from hlcli.exchange.base import Exchange
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.protect import requires_native_protection
from hlcli.safety.alerts import Alerter
from hlcli.sentry.apply import manage_open_trades
from hlcli.sentry.engine import active
from hlcli.state.store import StateStore, open_state

app = typer.Typer(no_args_is_help=True, help="In-trade manager (sentry).")

_MAX_BACKOFF_SECONDS = 600.0


def _env(g: GlobalState, *, for_write: bool) -> tuple[Exchange, StateStore, Caps, TunableConfig]:
    caps = get_caps()
    state = open_state(caps, g.network)
    tunable = load_tunable()
    if g.network is Network.PAPER:
        exchange: Exchange = PaperExchange(caps.starting_equity, state=state)
    else:
        exchange = build_for(g, for_write=for_write)
    return exchange, state, caps, tunable


def _pass(g: GlobalState, exchange: Exchange, state: StateStore, tunable: TunableConfig,
          alerter: Alerter | None) -> dict:
    summary = manage_open_trades(
        exchange, state, tunable, time.time(),
        native_protected=requires_native_protection(g.network),
        dry_run=g.dry_run, alerter=alerter,
    )
    return {"network": g.network.value, "stops_moved": summary.stops_moved,
            "scaled_out": summary.scaled_out, "failed": summary.failed,
            "actions": summary.actions,
            "note": "dry-run (no state changes)" if g.dry_run else "ok"}


@app.command("once")
def once(ctx: typer.Context) -> None:
    """One management pass over every open trade."""
    g = state_of(ctx)
    exchange, state, caps, tunable = _env(g, for_write=not g.dry_run)
    if not active(tunable.trail):
        note("trail rules are all off — nothing to manage (see `config show` → trail)")
    alerter = None if g.dry_run else Alerter(caps.data_dir / f"alerts-{g.network.value}.log")
    emit(_pass(g, exchange, state, tunable, alerter), as_json=g.json_out, title="sentry once")


@app.command("run")
def run(ctx: typer.Context, interval: float = typer.Option(60.0, "--interval", help="seconds between passes")) -> None:
    """Continuous management loop (ctrl-c to stop). Entries/close-outs stay with `hl exec run`."""
    g = state_of(ctx)
    exchange, state, caps, _ = _env(g, for_write=True)
    alerter = Alerter(caps.data_dir / f"alerts-{g.network.value}.log")
    note(f"sentry running every {interval}s on {g.network.value} — ctrl-c to stop")
    failures = 0
    try:
        while True:
            try:
                # Re-read the tunable surface each pass, same contract as `exec run`.
                s = _pass(g, exchange, state, load_tunable(), alerter)
                failures = 0
                note(f"[dim]{time.strftime('%H:%M:%S')}[/dim] stops_moved={s['stops_moved']} "
                     f"scaled_out={s['scaled_out']} failed={s['failed']}")
            except Exception as exc:  # keep the loop alive across transient feed faults
                failures += 1
                note(f"[yellow]{time.strftime('%H:%M:%S')} pass failed ({failures}x): {exc}[/yellow]")
            time.sleep(min(interval * (2 ** min(failures, 10)), _MAX_BACKOFF_SECONDS) if failures else interval)
    except KeyboardInterrupt:
        note("stopped")


@app.command("status")
def status(ctx: typer.Context) -> None:
    """Open trades through the manager's eyes: unrealized R, working vs initial stop."""
    g = state_of(ctx)
    exchange, state, _, tunable = _env(g, for_write=False)
    marks = exchange.get_marks()
    rows = []
    for t in state.open_trades():
        mark = marks.get(t["coin"])
        initial = t["initial_sl"] or t["sl"]
        risk = abs(t["entry"] - initial)
        r_now = None
        if mark is not None and risk > 0:
            favorable = (mark - t["entry"]) if Side(t["side"]) is Side.LONG else (t["entry"] - mark)
            r_now = round(favorable / risk, 2)
        rows.append({
            "id": t["id"], "coin": t["coin"], "side": t["side"], "size": t["size"],
            "entry": t["entry"], "mark": mark, "r_now": r_now,
            "sl": t["sl"], "initial_sl": initial, "tp": t["tp"],
            "scaled_out": bool(t["scaled_out"]), "shadow": bool(t["shadow"]),
        })
    emit_rows(rows, as_json=g.json_out, title=f"sentry status ({g.network.value})")
    if not g.json_out:
        note(f"trail config: {tunable.trail.model_dump()}")


@app.command("log")
def log(ctx: typer.Context, limit: int = typer.Option(50, "--limit")) -> None:
    """Recent management actions (the sentry audit trail)."""
    g = state_of(ctx)
    state = open_state(get_caps(), g.network)
    emit_rows(state.recent_sentry(limit), as_json=g.json_out, title="sentry log")
