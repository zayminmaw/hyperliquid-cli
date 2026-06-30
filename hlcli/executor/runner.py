"""The executor pass (PLAN.md §5).

One pass: resolve positions/equity → pull new candidates past the high-water mark
→ enrich → LLM decision → risk gate → fire approved (idempotent) → log → advance
the HWM. The judgment/mechanics split means the gate is identical whether the
decision came from the LLM or a deterministic stub.

Three knobs shape a pass:
  - `dry_run`     compute everything, mutate nothing (a side-effect-free preview).
  - `fire_enabled=False`  shadow mode — decide, gate, and log, but fire nothing.
  - `decide_fn`   injected so tests exercise the mechanics with a deterministic
                  decider (the real LLM call is mocked, never hit in tests).

A *schema-invalid* decision is dropped, tallied, and logged — never fired, never
guessed at. An API failure propagates out of the pass for the caller to handle.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from pydantic import BaseModel

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.executor.decision import DecisionResult, decide
from hlcli.executor.enrich import enrich
from hlcli.executor.execute import fire
from hlcli.executor.gate import GateContext, evaluate
from hlcli.executor.protect import emergency_close, place_protection, requires_native_protection
from hlcli.executor.resolve import resolve_open_trades
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.state.store import StateStore

DecideFn = Callable[..., DecisionResult]


class PassSummary(BaseModel):
    network: Network
    seen: int
    approved: int
    fired: int
    rejected: int
    dropped: int
    resolved: int
    note: str


def run_once(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    dry_run: bool = False,
    fire_enabled: bool = True,
    decide_fn: DecideFn = decide,
    alerter: Alerter | None = None,
    now: float | None = None,
) -> PassSummary:
    now = now if now is not None else time.time()
    breaker = Breaker(state, caps)
    protected = requires_native_protection(exchange.network)

    marks = exchange.get_marks()
    # Monitor step: close any open trade whose SL/TP/expiry has triggered, recording
    # its outcome. Skipped for shadow/dry-run (no live book to manage).
    resolved = (
        resolve_open_trades(exchange, state, caps, tunable, now, marks=marks, native_protected=protected)
        if fire_enabled and not dry_run else 0
    )

    equity = exchange.equity()
    positions = exchange.get_positions()
    open_coins = {p.coin for p in positions}
    realized = state.paper_realized() if exchange.network is Network.PAPER else None
    recent = state.recent_decisions(limit=10)
    breaker_tripped = breaker.tripped()
    daily_loss = breaker.daily_loss_hit(equity)

    batch = state.pull_new(limit=tunable.max_candidates_per_pass)
    if batch and (breaker_tripped or daily_loss):
        _emit(alerter, "halted", level="critical",
              reason="kill switch" if breaker_tripped else "daily loss limit", candidates=len(batch))
    approved = fired = rejected = dropped = 0

    for seq, candidate in batch:
        ctx = enrich(
            candidate, marks=marks, equity=equity, positions=positions,
            realized=realized, recent=recent, tunable=tunable,
        )
        result = decide_fn(ctx, caps, tunable)

        if result.dropped:
            dropped += 1
            if not dry_run:
                state.log_decision(candidate.id, now, context={"dropped": result.note, "raw": result.raw})
                state.set_status(seq, "dropped")
                state.advance_hwm(seq)
            continue

        decision = result.decision
        gate_ctx = GateContext(
            caps=caps, tunable=tunable, equity=equity,
            open_coins=set(open_coins), open_count=len(open_coins),
            now=now, breaker_tripped=breaker_tripped, daily_loss_hit=daily_loss,
            regime=ctx.regime,
        )
        outcome = evaluate(candidate, decision, gate_ctx)

        if dry_run:
            approved += int(outcome.approved)
            rejected += int(not outcome.approved)
            continue  # side-effect free

        fill = None
        status = "rejected"
        if not outcome.approved:
            rejected += 1
            _emit(alerter, "reject", level="warning", coin=candidate.coin, reason=outcome.reason)
        elif not fire_enabled:
            approved += 1
            status = "shadow"  # approved but deliberately not fired
        else:
            approved += 1
            fill = fire(exchange, state, candidate, outcome.order, now)
            if not fill.accepted:
                rejected += 1  # duplicate or exchange reject
                _emit(alerter, "reject", level="warning", coin=candidate.coin,
                      reason=fill.message or fill.status)
            elif protected and not _secure(exchange, candidate, outcome.order.size, alerter):
                rejected += 1  # entry filled but couldn't be protected → emergency-closed
                status = "aborted"
            else:
                fired += 1
                open_coins.add(candidate.coin)
                status = "fired"
                state.open_trade(
                    candidate.id, candidate.coin, candidate.side, candidate.entry,
                    candidate.sl, candidate.tp, outcome.order.size,
                    decision.conviction, ctx.regime, now,
                )
                _emit(alerter, "fire", level="info", coin=candidate.coin, side=candidate.side.value,
                      size=outcome.order.size, conviction=decision.conviction, order_id=fill.order_id)

        state.log_decision(
            candidate.id, now, decision=decision, gate=outcome, fill=fill,
            context={"equity": equity, "open_coins": sorted(open_coins), "regime": ctx.regime},
        )
        state.set_status(seq, status)
        state.advance_hwm(seq)

    return PassSummary(
        network=exchange.network, seen=len(batch), approved=approved,
        fired=fired, rejected=rejected, dropped=dropped, resolved=resolved,
        note=_note(dry_run=dry_run, fire_enabled=fire_enabled),
    )


def _secure(exchange: Exchange, candidate, size: float, alerter: Alerter | None) -> bool:
    """Place native protective triggers; if that fails, flatten the position rather than
    leave it naked. Returns True only when the position is protected."""
    protection = place_protection(exchange, candidate, size)
    if protection.ok:
        return True
    closed = emergency_close(exchange, candidate, size)
    _emit(alerter, "protection_failed", level="critical", coin=candidate.coin,
          reason=protection.failed, emergency_closed=closed.accepted)
    return False


def _emit(alerter: Alerter | None, event: str, **fields) -> None:
    if alerter is not None:
        alerter.alert(event, **fields)


def _note(*, dry_run: bool, fire_enabled: bool) -> str:
    if dry_run:
        return "dry-run (no state changes)"
    if not fire_enabled:
        return "shadow (logged, fired nothing)"
    return "ok"
