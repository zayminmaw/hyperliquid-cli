"""The executor pass (PLAN.md §5).

One pass: resolve positions/equity → re-check any due WAIT deferrals → pull new
candidates past the high-water mark → enrich → LLM decision → risk gate → fire
approved (idempotent) → log → advance the HWM. The judgment/mechanics split means
the gate is identical whether the decision came from the LLM or a deterministic stub.

A `wait` verdict is **deferred**, not rejected: the candidate is parked and re-checked
with fresh data up to `caps.followup_max_attempts` times, each re-check scheduled to
land inside the candidate's freshness window. The gate stays pure — it still rejects a
`wait` it's handed; the runner just intercepts `wait` before the gate to defer instead.

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
from dataclasses import dataclass, field

import httpx
from pydantic import BaseModel

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Action, Candidate, Network, OrderResult, Position, Timing
from hlcli.exchange.base import Exchange
from hlcli.executor.decision import DecisionResult, decide
from hlcli.executor.enrich import enrich
from hlcli.executor.execute import fire
from hlcli.executor.gate import GateContext, evaluate
from hlcli.executor.protect import emergency_close, place_protection, requires_native_protection
from hlcli.executor.regime import classify, summarize
from hlcli.executor.resolve import resolve_open_trades
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.state.store import DeferredCandidate, StateStore

DecideFn = Callable[..., DecisionResult]

# Scheduling bounds for a WAIT re-check (the runner clamps the LLM's request into these).
_MIN_RECHECK_SECONDS = 60.0  # don't re-check sooner than this — avoids hot-looping a pass
_DEFAULT_RECHECK_MINUTES = 5.0  # used when the model gives no usable recheck time


class PassSummary(BaseModel):
    network: Network
    seen: int
    rechecked: int
    approved: int
    fired: int
    rejected: int
    dropped: int
    deferred: int
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

    # WAIT re-checks (skip in a dry-run preview, and while the kill switch is tripped — a
    # re-check can't fire anyway, so don't spend an LLM call or a follow-up attempt on it;
    # parked candidates wait, attempts intact, until the breaker clears).
    due = state.due_deferred(now) if (not dry_run and not breaker_tripped) else []
    batch = state.pull_new(limit=tunable.max_candidates_per_pass)
    if alerter is not None and not dry_run:
        _alert_halt(state, alerter, batch, breaker_tripped, daily_loss)

    coins = {c.coin for _, c in batch} | {d.candidate.coin for d in due}
    common = _PassContext(
        caps=caps, tunable=tunable, decide_fn=decide_fn, marks=marks,
        market=_market_context(exchange, coins), equity=equity, positions=positions,
        realized=realized, recent=recent, open_coins=open_coins, now=now,
        breaker_tripped=breaker_tripped, daily_loss=daily_loss, protected=protected,
        fire_enabled=fire_enabled, dry_run=dry_run, alerter=alerter,
    )

    # Re-check due deferrals first: they were promised a look now, and a re-check that
    # fires should count toward max-concurrent before this pass's fresh candidates.
    for d in due:
        _process_deferred(exchange, state, d, common)

    for seq, candidate in batch:
        step = _evaluate(exchange, state, candidate, common, attempts_left=caps.followup_max_attempts)
        if dry_run:
            continue  # side-effect free (counters already tallied)
        if step.status == "deferred":
            state.defer_candidate(candidate, step.next_check_at, step.attempts_remaining)
        state.set_status(seq, step.status)
        state.advance_hwm(seq)

    t = common.tally
    return PassSummary(
        network=exchange.network, seen=len(batch), rechecked=len(due),
        approved=t.approved, fired=t.fired, rejected=t.rejected, dropped=t.dropped,
        deferred=t.deferred, resolved=resolved,
        note=_note(dry_run=dry_run, fire_enabled=fire_enabled),
    )


@dataclass
class _Tally:
    approved: int = 0
    fired: int = 0
    rejected: int = 0
    dropped: int = 0
    deferred: int = 0


@dataclass
class _PassContext:
    """Everything one pass shares across candidates. `open_coins` and `tally` are mutated
    as candidates are processed; the rest is read-only per-pass state."""

    caps: Caps
    tunable: TunableConfig
    decide_fn: DecideFn
    marks: dict[str, float]
    market: dict[str, tuple]  # coin → (candle summary, regime); see _market_context
    equity: float
    positions: list[Position]
    realized: float | None
    recent: list[dict]
    open_coins: set[str]
    now: float
    breaker_tripped: bool
    daily_loss: bool
    protected: bool
    fire_enabled: bool
    dry_run: bool
    alerter: Alerter | None
    tally: _Tally = field(default_factory=_Tally)


@dataclass
class _Step:
    """What `_evaluate` did with one candidate. `status` routes persistence; the
    scheduling fields are set only when `status == "deferred"`."""

    status: str
    next_check_at: float = 0.0
    attempts_remaining: int = 0


def _process_deferred(exchange: Exchange, state: StateStore, d: DeferredCandidate, common: _PassContext) -> None:
    """Re-check a due deferral against fresh data. This re-check consumes one attempt;
    a repeat WAIT reschedules with what's left, anything else is terminal."""
    step = _evaluate(exchange, state, d.candidate, common, attempts_left=d.attempts_remaining - 1)
    if step.status == "deferred":
        state.defer_candidate(d.candidate, step.next_check_at, step.attempts_remaining)
    else:
        state.drop_deferred(d.candidate.id)


def _evaluate(exchange: Exchange, state: StateStore, candidate: Candidate, common: _PassContext, *, attempts_left: int) -> _Step:
    """enrich → decide → (drop | WAIT-defer | gate → fire) → log, tallying as it goes.
    Returns a `_Step` describing the outcome; the caller owns intake/deferred persistence."""
    candles, regime = common.market[candidate.coin]  # built from this pass's batch ∪ due coins
    ctx = enrich(
        candidate, marks=common.marks, equity=common.equity, positions=common.positions,
        realized=common.realized, recent=common.recent, tunable=common.tunable,
        candles=candles, regime=regime,
    )
    result = common.decide_fn(ctx, common.caps, common.tunable)

    if result.dropped:
        common.tally.dropped += 1
        if not common.dry_run:
            state.log_decision(candidate.id, common.now, context={"dropped": result.note, "raw": result.raw})
        return _Step("dropped")

    decision = result.decision

    # Intercept WAIT before the gate: park it for a fresh re-check instead of rejecting.
    if decision.action is Action.ACT and decision.timing is Timing.WAIT:
        return _wait(state, candidate, decision, regime, common, attempts_left)

    gate_ctx = GateContext(
        caps=common.caps, tunable=common.tunable, equity=common.equity,
        open_coins=set(common.open_coins), open_count=len(common.open_coins),
        now=common.now, breaker_tripped=common.breaker_tripped, daily_loss_hit=common.daily_loss,
        regime=regime,
    )
    outcome = evaluate(candidate, decision, gate_ctx)

    if common.dry_run:
        common.tally.approved += int(outcome.approved)
        common.tally.rejected += int(not outcome.approved)
        return _Step("approved" if outcome.approved else "rejected")

    fill = None
    if not outcome.approved:
        common.tally.rejected += 1
        status = "rejected"
        _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason=outcome.reason)
    elif not common.fire_enabled:
        common.tally.approved += 1
        status = "shadow"  # approved but deliberately not fired
    else:
        common.tally.approved += 1
        status, fill = _fire_and_reconcile(exchange, state, candidate, decision, outcome, regime, common)

    state.log_decision(
        candidate.id, common.now, decision=decision, gate=outcome, fill=fill,
        context={"equity": common.equity, "open_coins": sorted(common.open_coins), "regime": regime},
    )
    return _Step(status)


def _fire_and_reconcile(exchange, state, candidate, decision, outcome, regime, common: _PassContext) -> tuple[str, OrderResult]:
    """Fire an approved order and reconcile against what actually filled — not what we
    intended. Returns (status, fill); opens the ledger + native protection only on a real fill."""
    fill = fire(exchange, state, candidate, outcome.order, common.now)
    filled = fill.filled_size if fill.filled_size is not None else outcome.order.size
    entry_price = fill.avg_price if fill.avg_price is not None else candidate.entry
    if not fill.accepted:
        common.tally.rejected += 1  # duplicate or exchange reject
        _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason=fill.message or fill.status)
        return "rejected", fill
    if filled <= 0:
        common.tally.rejected += 1  # accepted but nothing filled (rested/canceled) — no position
        _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason="unfilled")
        return "unfilled", fill
    if common.protected and not _secure(exchange, candidate, filled, common.alerter):
        common.tally.rejected += 1  # filled but couldn't be protected → emergency-closed
        return "aborted", fill
    common.tally.fired += 1
    common.open_coins.add(candidate.coin)
    state.open_trade(
        candidate.id, candidate.coin, candidate.side, entry_price,
        candidate.sl, candidate.tp, filled, decision.conviction, regime, common.now,
    )
    _emit(common.alerter, "fire", level="info", coin=candidate.coin, side=candidate.side.value,
          size=filled, conviction=decision.conviction, order_id=fill.order_id)
    return "fired", fill


def _wait(state: StateStore, candidate: Candidate, decision, regime, common: _PassContext, attempts_left: int) -> _Step:
    """Defer an act+wait verdict for a later re-check — as long as an attempt remains and
    there's freshness room left; otherwise it's a terminal reject (like the gate's WAIT path)."""
    next_at = _schedule_recheck(candidate, decision.recheck_in_minutes, common.now, common.caps) if attempts_left >= 1 else None
    if next_at is None:
        common.tally.rejected += 1
        reason = "wait: out of attempts" if attempts_left < 1 else "wait: would be stale before re-check"
        if not common.dry_run:
            state.log_decision(candidate.id, common.now, decision=decision, context={"wait": reason, "regime": regime})
        return _Step("rejected")
    common.tally.deferred += 1
    if not common.dry_run:
        state.log_decision(
            candidate.id, common.now, decision=decision,
            context={"wait": "deferred", "next_check_at": next_at, "attempts_remaining": attempts_left, "regime": regime},
        )
    return _Step("deferred", next_check_at=next_at, attempts_remaining=attempts_left)


def _schedule_recheck(candidate: Candidate, recheck_minutes: float | None, now: float, caps: Caps) -> float | None:
    """When to next look at this candidate, clamped to land inside its freshness window.
    None when there's no room left for a meaningful re-check before it goes stale.

    Stage 2 of the two-stage recheck clamp: `decision._clamp_recheck` first bounds the
    model's raw value to [0, _RECHECK_CEILING_MIN]; here we bound the scheduled *time*
    to [now + _MIN_RECHECK_SECONDS, freshness boundary]."""
    latest = candidate.created_at + caps.max_signal_age_minutes * 60
    if latest - now < _MIN_RECHECK_SECONDS:
        return None
    minutes = recheck_minutes if (recheck_minutes and recheck_minutes > 0) else _DEFAULT_RECHECK_MINUTES
    return min(now + max(_MIN_RECHECK_SECONDS, minutes * 60), latest)


def _market_context(exchange: Exchange, coins: set[str]) -> dict[str, tuple]:
    """coin → (compact candle summary, regime), fetched once per coin for this pass."""
    return {coin: _coin_context(exchange, coin) for coin in coins}


def _coin_context(exchange: Exchange, coin: str) -> tuple:
    bars = _fetch_candles(exchange, coin)
    return summarize(bars), classify(bars)


def _fetch_candles(exchange: Exchange, coin: str):
    # Best-effort: candles are decision *context*, not a safety input, so a feed hiccup
    # degrades this coin to "no context" rather than aborting a pass that already has
    # valid marks. (A marks failure still aborts — that read is load-bearing.)
    # Catch only genuine feed failures — network/HTTP errors and a malformed or
    # unexpected response shape; an unexpected bug (e.g. a programming error) still
    # surfaces rather than masquerading as "no candles".
    try:
        return exchange.get_candles(coin)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return []


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


_HALT_ALERT_KEY = "alert_halt_last"


def _alert_halt(state: StateStore, alerter: Alerter, batch, breaker_tripped: bool, daily_loss: bool) -> None:
    """Alert when the breaker / loss-limit is blocking candidates — but only on a
    *change* of state, so a tripped breaker doesn't spam the run loop every pass."""
    reason = "kill switch" if breaker_tripped else "daily loss limit" if daily_loss else ""
    if reason and batch:
        if state.meta_get(_HALT_ALERT_KEY) != reason:
            alerter.alert("halted", level="critical", reason=reason, candidates=len(batch))
            state.meta_set(_HALT_ALERT_KEY, reason)
    elif not reason and state.meta_get(_HALT_ALERT_KEY):
        state.meta_set(_HALT_ALERT_KEY, "")  # cleared — re-arm for the next trip


def _note(*, dry_run: bool, fire_enabled: bool) -> str:
    if dry_run:
        return "dry-run (no state changes)"
    if not fire_enabled:
        return "shadow (logged, fired nothing)"
    return "ok"
