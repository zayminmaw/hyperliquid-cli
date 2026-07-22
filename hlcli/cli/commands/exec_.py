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
from hlcli.core.backoff import backoff_delay
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.executor.intake import make_candidate, parse_batch
from hlcli.executor.monitor import position_health
from hlcli.executor.reconcile import reconcile
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import network_alerter
from hlcli.safety.breaker import Breaker
from hlcli.safety.graduation import assess, graded_trades
from hlcli.state.store import StateStore, open_state
from hlcli.tuner.stats import (
    calibration_verdict,
    conviction_calibration,
    management_cohorts,
    performance,
    summary,
)

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
    direction: Optional[str] = typer.Option(
        None, "--direction", help="producer's own verdict, e.g. LONG|SHORT|WAIT (advisory)"),
    confidence: Optional[float] = typer.Option(
        None, "--confidence", help="producer's own confidence 0..1 (advisory)"),
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
            candidates = [make_candidate(coin, entry, tp, sl, reasoning=reason, news=news,
                                         source_direction=direction, source_confidence=confidence)]
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
    # Alerted like every other pass (O-2): shadow is exactly where the unmanaged-position
    # reconciliation must not be silent — it's the long-running pre-mainnet mode.
    summary = run_once(exchange, state, caps, tunable, fire_enabled=False, dry_run=g.dry_run,
                       alerter=network_alerter(caps, g.network))
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
            time.sleep(backoff_delay(interval, failures, _MAX_BACKOFF_SECONDS))
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


@app.command("reconcile")
def reconcile_cmd(
    ctx: typer.Context,
    halt: bool = typer.Option(True, "--halt/--no-halt", help="trip the kill switch if unsafe"),
) -> None:
    """Diff the exchange against the ledger (wave-2 G) → safe / requires-halt.

    On an unsafe divergence (an unexpected position, a size mismatch, or a live position with
    no native protection) it trips the breaker so a restart can't fire into an inconsistent
    book — run it after any crash, especially on mainnet. `--no-halt` reports only.
    """
    g = state_of(ctx)
    exchange, state, caps, _tunable = open_env(g, for_write=False)
    try:
        report = reconcile(exchange, state)
        tripped = False
        if report.requires_halt and halt and not g.dry_run:
            Breaker(state, caps).set(True)
            tripped = True
            network_alerter(caps, g.network).alert(
                "reconcile_halt", level="critical",
                divergences=[{"kind": d.kind, "coin": d.coin} for d in report.divergences])
        emit(
            {
                "network": g.network.value,
                "safe": report.is_safe,
                "requires_halt": report.requires_halt,
                "divergences": [{"kind": d.kind, "coin": d.coin, **d.detail} for d in report.divergences],
                "breaker_tripped": tripped,
            },
            as_json=g.json_out, title="exec reconcile",
        )
    finally:
        state.close()


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


def _arm_stats(trades: list[dict], caps) -> dict:
    """One decision-source book's headline expectancy — the A/B row for `--compare`.
    Graded via the shared `graded_trades` (partials + mechanical aborts + adopted rows
    excluded), so a scale-out ladder or a run of protection failures can't flatter one
    arm over another, and this can't drift from graduation's own grading rule."""
    graded = graded_trades(trades)
    s = summary(graded)
    perf = performance(graded, starting_equity=caps.starting_equity)
    cal = calibration_verdict(trades, min_bucket_n=caps.calibration_min_bucket_n,
                              min_spread_r=caps.calibration_min_spread_r)
    return {"n": s["n"], "win_rate": s["win_rate"], "avg_r": s["avg_r"],
            "total_realized": s["total_realized"], "profit_factor": perf["profit_factor"],
            "calibration_ready": cal["ready"]}


def _delta(b: dict, a: dict) -> dict:
    """b − a on the comparable scalars; a None on either side leaves that metric None."""
    out = {}
    for k in ("n", "win_rate", "avg_r", "total_realized", "profit_factor"):
        x, y = b.get(k), a.get(k)
        out[k] = round(x - y, 4) if isinstance(x, (int, float)) and isinstance(y, (int, float)) else None
    return out


@app.command("report")
def report(
    ctx: typer.Context,
    compare: Optional[Path] = typer.Option(
        None, "--compare",
        help="another HL_DATA_DIR; diff its book vs this one (the decision-source A/B, #5)"),
) -> None:
    """Account summary: equity, open positions, unrealized P&L, breaker + graduation readiness.

    `--compare <data_dir>` instead diffs two decision-source books head-to-head (expectancy,
    profit factor, calibration) — the readout for the llm-vs-rule-vs-follow_source A/B."""
    g = state_of(ctx)
    exchange, state, caps, _tunable = open_env(g, for_write=False)
    try:
        resolved = state.resolved_trades()

        if compare is not None:
            other_db = compare / f"state-{g.network.value}.db"
            if not other_db.exists():
                raise typer.BadParameter(f"no {g.network.value} book under {compare} (expected {other_db.name})")
            other = StateStore(other_db, read_only=True)  # compare must never mutate the other book
            try:
                b_trades = other.resolved_trades()
            finally:
                other.close()
            a, b = _arm_stats(resolved, caps), _arm_stats(b_trades, caps)
            emit(
                {"network": g.network.value,
                 "a": {"data_dir": str(caps.data_dir), **a},
                 "b": {"data_dir": str(compare), **b},
                 "delta_b_minus_a": _delta(b, a)},
                as_json=g.json_out, title="exec report --compare",
            )
            return

        positions = exchange.get_positions()
        emit(
            {
                "network": g.network.value,
                "equity": exchange.equity(),
                "open_positions": len(positions),
                "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4),
                "breaker": "tripped" if Breaker(state, caps).tripped() else "clear",
                "deferred": state.deferred_count(),  # WAIT candidates parked for re-check
                "graduation": assess(resolved, caps),
                # Risk-adjusted + path-risk view win-rate/expectancy hide (audit C/D): Sharpe,
                # Sortino, max drawdown, profit factor, and realized entry slippage.
                "performance": performance(resolved, starting_equity=caps.starting_equity),
                # The evidence gate for re-enabling conviction→size scaling (audit L-1/L-4):
                # scaling stays off until higher buckets show higher avg_r on real sample.
                "conviction_calibration": conviction_calibration(resolved),
                # Formal pass/fail on that gate: monotonic bucket avg_R + spread + sample floor.
                # `ready: false` is the precondition guarding any flip of `sizing.enabled`.
                "calibration_verdict": calibration_verdict(
                    resolved,
                    min_bucket_n=caps.calibration_min_bucket_n,
                    min_spread_r=caps.calibration_min_spread_r,
                ),
                # Realized R by which management events fired (audit J) — the evidence a sentry
                # tuner would act on: do trailed/scaled trades out-perform ones left on the initial stop?
                "management_cohorts": management_cohorts(resolved),
            },
            as_json=g.json_out, title="exec report",
        )
    finally:
        state.close()
