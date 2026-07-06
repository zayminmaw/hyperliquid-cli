"""`hl sentry` — the in-trade manager (PLAN.md §14).

The watch pass (`once`/`run`) covers sentry's two pools on its own cadence:
open positions (6a deterministic rules + resolve) and due WAIT deferrals
(re-checked through the existing decision + entry gate — sentry may *enter* a
parked setup, but it never consumes the intake stream; that stays with `hl exec`).

`shadow` is 6b: the LLM manager proposes an action per open position, logged next
to what the 6a baseline would do, firing nothing — the value-add evidence that
gates 6c.
"""

from __future__ import annotations

import json
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
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.sentry.apply import manage_open_trades
from hlcli.sentry.live import manage_live
from hlcli.sentry.shadow import shadow_pass
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


def _alerter(caps: Caps, network: Network) -> Alerter:
    return Alerter(caps.data_dir / f"alerts-{network.value}.log")


@app.command("once")
def once(ctx: typer.Context) -> None:
    """One watch pass: manage open trades, resolve, re-check due WAIT deferrals."""
    g = state_of(ctx)
    exchange, state, caps, tunable = _env(g, for_write=not g.dry_run)
    if g.dry_run:
        # A dry-run pass through the runner is fully side-effect-free and therefore
        # silent about the trail rules; previewing the engine's plan is more useful.
        s = manage_open_trades(exchange, state, tunable, time.time(),
                               native_protected=requires_native_protection(g.network), dry_run=True)
        emit({"network": g.network.value, "would_apply": s.actions,
              "note": "dry-run (no state changes)"}, as_json=g.json_out, title="sentry once")
        return
    summary = run_once(exchange, state, caps, tunable, include_intake=False,
                       alerter=_alerter(caps, g.network))
    emit(summary.model_dump(), as_json=g.json_out, title="sentry once")


@app.command("shadow")
def shadow(ctx: typer.Context) -> None:
    """6b: LLM management proposals per open position, logged vs the 6a baseline. Fires nothing."""
    g = state_of(ctx)
    exchange, state, caps, tunable = _env(g, for_write=False)
    breaker = Breaker(state, caps)
    s = shadow_pass(exchange, state, caps, tunable, breaker_tripped=breaker.tripped())
    emit({"network": g.network.value, "evaluated": s.evaluated, "held": s.held,
          "proposed": s.proposed, "agreed": s.agreed, "dropped": s.dropped,
          "actions": s.actions, "note": "shadow (logged, fired nothing)"},
         as_json=g.json_out, title="sentry shadow")


@app.command("manage")
def manage(ctx: typer.Context) -> None:
    """6c: one LIVE management pass — LLM verdicts through the management gate.
    Risk-reducing menu only; paper/testnet only until graduation (6d)."""
    g = state_of(ctx)
    _refuse_mainnet(g)
    exchange, state, caps, tunable = _env(g, for_write=True)
    s = manage_live(exchange, state, caps, tunable,
                    native_protected=requires_native_protection(g.network),
                    alerter=_alerter(caps, g.network))
    emit({"network": g.network.value, "evaluated": s.evaluated, "held": s.held,
          "applied": s.applied, "rejected": s.rejected, "dropped": s.dropped,
          "spaced": s.spaced, "failed": s.failed, "actions": s.actions, "note": s.note},
         as_json=g.json_out, title="sentry manage")


def _refuse_mainnet(g: GlobalState) -> None:
    if g.network is Network.MAINNET:
        raise typer.BadParameter(
            "sentry live management is paper/testnet only in Phase 6c "
            "(mainnet management arrives with graduation in 6d)")


@app.command("run")
def run(
    ctx: typer.Context,
    interval: float = typer.Option(60.0, "--interval", help="seconds between passes"),
    with_shadow: bool = typer.Option(False, "--shadow", help="also log LLM proposals vs the baseline each pass"),
    with_manage: bool = typer.Option(False, "--manage", help="6c: also apply gated LLM management actions each pass"),
) -> None:
    """Continuous watch loop (ctrl-c to stop). Intake stays with `hl exec run`."""
    g = state_of(ctx)
    if with_shadow and with_manage:
        raise typer.BadParameter("--shadow and --manage are exclusive: manage already logs "
                                 "every proposal, so shadowing on top doubles the LLM spend")
    if with_manage:
        _refuse_mainnet(g)
    exchange, state, caps, _ = _env(g, for_write=True)
    alerter = _alerter(caps, g.network)
    mode = " (+shadow)" if with_shadow else " (+manage)" if with_manage else ""
    note(f"sentry running every {interval}s on {g.network.value}{mode} — ctrl-c to stop")
    failures = 0
    try:
        while True:
            try:
                tunable = load_tunable()  # re-read each pass, same contract as `exec run`
                if with_shadow:
                    # Propose BEFORE the rules mutate the book, so proposal and
                    # baseline judge the same state.
                    sh = shadow_pass(exchange, state, caps, tunable,
                                     breaker_tripped=Breaker(state, caps).tripped())
                    note(f"[dim]shadow[/dim] evaluated={sh.evaluated} proposed={sh.proposed} "
                         f"agreed={sh.agreed} dropped={sh.dropped}")
                if with_manage:
                    # Judgment acts on the raw state; the rule pass below then
                    # guards whatever remains.
                    lv = manage_live(exchange, state, caps, tunable,
                                     native_protected=requires_native_protection(g.network),
                                     alerter=alerter)
                    note(f"[dim]manage[/dim] evaluated={lv.evaluated} applied={lv.applied} "
                         f"held={lv.held} rejected={lv.rejected} dropped={lv.dropped}")
                s = run_once(exchange, state, caps, tunable, include_intake=False, alerter=alerter)
                failures = 0
                note(f"[dim]{time.strftime('%H:%M:%S')}[/dim] managed={s.managed} "
                     f"resolved={s.resolved} rechecked={s.rechecked} fired={s.fired} "
                     f"deferred={s.deferred} rejected={s.rejected}")
            except Exception as exc:  # keep the loop alive across transient LLM/feed faults
                failures += 1
                note(f"[yellow]{time.strftime('%H:%M:%S')} pass failed ({failures}x): {exc}[/yellow]")
                if failures == 1:
                    alerter.alert("pass_failed", level="warning", consecutive=failures, error=str(exc))
            time.sleep(min(interval * (2 ** min(failures, 10)), _MAX_BACKOFF_SECONDS) if failures else interval)
    except KeyboardInterrupt:
        note("stopped")


@app.command("status")
def status(ctx: typer.Context) -> None:
    """Open trades through the manager's eyes, plus the shadow value-add tally."""
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
    shadow_stats = _shadow_stats(state)
    if g.json_out:
        emit(shadow_stats, as_json=True)
    else:
        note(f"shadow: {shadow_stats}")
        note(f"trail config: {tunable.trail.model_dump()}")


def _shadow_stats(state: StateStore, window: int = 200) -> dict:
    """Proposal tally over the recent shadow log — the 6b value-add scoreboard."""
    rows = [r for r in state.recent_sentry(window) if r["action"] in ("shadow", "shadow_dropped")]
    proposals = [json.loads(r["details"]) for r in rows if r["action"] == "shadow"]
    by_action: dict[str, int] = {}
    for p in proposals:
        a = p["proposal"]["action"]
        by_action[a] = by_action.get(a, 0) + 1
    return {
        "proposals": len(proposals),
        "dropped": sum(1 for r in rows if r["action"] == "shadow_dropped"),
        "agreed_with_baseline": sum(1 for p in proposals if p.get("agrees")),
        "by_action": by_action,
    }


@app.command("log")
def log(ctx: typer.Context, limit: int = typer.Option(50, "--limit")) -> None:
    """Recent management actions and shadow proposals (the sentry audit trail)."""
    g = state_of(ctx)
    state = open_state(get_caps(), g.network)
    emit_rows(state.recent_sentry(limit), as_json=g.json_out, title="sentry log")
