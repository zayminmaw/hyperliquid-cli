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

from hlcli.cli.context import GlobalState, open_env, state_of
from hlcli.cli.output import emit, emit_rows, note
from hlcli.core.backoff import backoff_delay
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.core.types import Network, Side
from hlcli.executor.protect import requires_native_protection
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import network_alerter
from hlcli.safety.breaker import Breaker
from hlcli.sentry.adopt import adopt_unmanaged
from hlcli.sentry.apply import manage_open_trades
from hlcli.sentry.live import graduation_for_management, manage_live
from hlcli.sentry.shadow import shadow_pass
from hlcli.state.store import StateStore, open_state
from hlcli.tuner.stats import sentry_exit_attribution

app = typer.Typer(no_args_is_help=True, help="In-trade manager (sentry).")

_MAX_BACKOFF_SECONDS = 600.0


def require_exclusive_modes(with_shadow: bool, with_manage: bool) -> None:
    """`--shadow` and `--manage` can't run together: manage already logs every proposal,
    so shadowing on top just doubles the LLM spend. Shared by `sentry run` and `agent run`."""
    if with_shadow and with_manage:
        raise typer.BadParameter("--shadow and --manage are exclusive: manage already logs "
                                 "every proposal, so shadowing on top doubles the LLM spend")


@app.command("once")
def once(ctx: typer.Context) -> None:
    """One watch pass: manage open trades, resolve, re-check due WAIT deferrals."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=not g.dry_run)
    if g.dry_run:
        # A dry-run pass through the runner is fully side-effect-free and therefore
        # silent about the trail rules; previewing the engine's plan is more useful.
        s = manage_open_trades(exchange, state, tunable, time.time(),
                               native_protected=requires_native_protection(g.network), dry_run=True,
                               taker_fee_pct=caps.taker_fee_pct)
        emit({"network": g.network.value, "would_apply": s.actions,
              "note": "dry-run (no state changes)"}, as_json=g.json_out, title="sentry once")
        return
    alerter = network_alerter(caps, g.network)
    adopt_unmanaged(exchange, state, alerter=alerter)  # Mode A positions join the book first
    summary = run_once(exchange, state, caps, tunable, include_intake=False, alerter=alerter)
    emit(summary.model_dump(), as_json=g.json_out, title="sentry once")


@app.command("shadow")
def shadow(ctx: typer.Context) -> None:
    """6b: LLM management proposals per open position, logged vs the 6a baseline. Fires nothing."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=False)
    breaker = Breaker(state, caps)
    s = shadow_pass(exchange, state, caps, tunable, breaker_tripped=breaker.tripped())
    emit({"network": g.network.value, "evaluated": s.evaluated, "held": s.held,
          "proposed": s.proposed, "agreed": s.agreed, "dropped": s.dropped,
          "spaced": s.spaced, "actions": s.actions, "note": s.note},
         as_json=g.json_out, title="sentry shadow")


@app.command("manage")
def manage(ctx: typer.Context) -> None:
    """One LIVE management pass — LLM verdicts through the management gate
    (tighten/reduce/close/extend_tp, plus gated ADD). Mainnet requires graduation."""
    g = state_of(ctx)
    check_mainnet_graduation(g)
    exchange, state, caps, tunable = open_env(g, for_write=True)
    s = manage_live(exchange, state, caps, tunable,
                    native_protected=requires_native_protection(g.network),
                    alerter=network_alerter(caps, g.network))
    emit({"network": g.network.value, "evaluated": s.evaluated, "held": s.held,
          "applied": s.applied, "rejected": s.rejected, "dropped": s.dropped,
          "spaced": s.spaced, "failed": s.failed, "actions": s.actions, "note": s.note},
         as_json=g.json_out, title="sentry manage")


def check_mainnet_graduation(g: GlobalState) -> None:
    """Mainnet management must be EARNED on the testnet book (§14): enough resolved
    trades, over enough days, with positive expectancy — the same graduation that
    gates the executor's first real order."""
    if g.network is not Network.MAINNET:
        return
    verdict = graduation_for_management(get_caps())
    if not verdict["ready"]:
        failed = ", ".join(k for k, ok in verdict["checks"].items() if not ok)
        raise typer.BadParameter(
            f"sentry management on mainnet requires graduation on the testnet book — "
            f"failing: {failed} (n={verdict['n']}, avg_r={verdict['avg_r']}, "
            f"span_days={verdict['span_days']})")


@app.command("run")
def run(
    ctx: typer.Context,
    interval: float = typer.Option(60.0, "--interval", help="seconds between passes"),
    with_shadow: bool = typer.Option(False, "--shadow", help="also log LLM proposals vs the baseline each pass"),
    with_manage: bool = typer.Option(False, "--manage", help="6c: also apply gated LLM management actions each pass"),
) -> None:
    """Continuous watch loop (ctrl-c to stop). Intake stays with `hl exec run`."""
    g = state_of(ctx)
    require_exclusive_modes(with_shadow, with_manage)
    if with_manage:
        check_mainnet_graduation(g)
    exchange, state, caps, _ = open_env(g, for_write=True)
    alerter = network_alerter(caps, g.network)
    mode = " (+shadow)" if with_shadow else " (+manage)" if with_manage else ""
    note(f"sentry running every {interval}s on {g.network.value}{mode} — ctrl-c to stop")
    failures = 0
    try:
        while True:
            try:
                tunable = load_tunable()  # re-read each pass, same contract as `exec run`
                ad = adopt_unmanaged(exchange, state, alerter=alerter)
                if ad.adopted:
                    note(f"[dim]adopted[/dim] {', '.join(a['coin'] for a in ad.adopted)}")
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
            time.sleep(backoff_delay(interval, failures, _MAX_BACKOFF_SECONDS))
    except KeyboardInterrupt:
        note("stopped")


@app.command("adopt")
def adopt(ctx: typer.Context) -> None:
    """Adopt unmanaged (Mode A) positions that carry an exchange stop trigger into
    the ledger — recorded only, no orders placed. Stopless positions are skipped:
    set a stop with `hl trade order stop-loss` first (adoption never invents one)."""
    g = state_of(ctx)
    exchange, state, caps, _ = open_env(g, for_write=False)
    s = adopt_unmanaged(exchange, state, alerter=network_alerter(caps, g.network))
    emit({"network": g.network.value, "adopted": s.adopted, "skipped": s.skipped},
         as_json=g.json_out, title="sentry adopt")


@app.command("status")
def status(ctx: typer.Context) -> None:
    """Open trades through the manager's eyes, plus the shadow value-add tally."""
    g = state_of(ctx)
    exchange, state, _, tunable = open_env(g, for_write=False)
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
    """Proposal tally over the recent shadow log — the 6b value-add scoreboard. Beyond the
    agreement count, `exit_attribution` scores diverging early-exit proposals on realized R
    (audit J): would following the LLM's close/reduce calls have banked more R than the rules?"""
    rows = [r for r in state.recent_sentry(window) if r["action"] in ("shadow", "shadow_dropped")]
    proposals = [{"trade_id": r["trade_id"], **json.loads(r["details"])}
                 for r in rows if r["action"] == "shadow"]
    by_action: dict[str, int] = {}
    for p in proposals:
        a = p["proposal"]["action"]
        by_action[a] = by_action.get(a, 0) + 1
    final_r = {t["id"]: t["r_multiple"] for t in state.resolved_trades() if t["r_multiple"] is not None}
    return {
        "proposals": len(proposals),
        "dropped": sum(1 for r in rows if r["action"] == "shadow_dropped"),
        "agreed_with_baseline": sum(1 for p in proposals if p.get("agrees")),
        "by_action": by_action,
        "exit_attribution": sentry_exit_attribution(proposals, final_r),
    }


@app.command("log")
def log(ctx: typer.Context, limit: int = typer.Option(50, "--limit")) -> None:
    """Recent management actions and shadow proposals (the sentry audit trail)."""
    g = state_of(ctx)
    state = open_state(get_caps(), g.network)
    emit_rows(state.recent_sentry(limit), as_json=g.json_out, title="sentry log")
