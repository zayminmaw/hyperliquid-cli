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
from hlcli.core.types import Action, Candidate, Network, OrderResult, Position, Side, Timing
from hlcli.exchange.base import Exchange
from hlcli.executor.decision import DecisionResult, decider_for
from hlcli.executor.enrich import enrich
from hlcli.executor.execute import fire
from hlcli.executor.intake import injection_flags
from hlcli.executor.gate import GateContext, evaluate
from hlcli.executor.protect import (
    cancel_placed,
    emergency_close,
    place_protection,
    requires_native_protection,
)
from hlcli.executor.regime import DECISION_INTERVAL, classify, summarize
from hlcli.executor.resolve import resolve_open_trades
from hlcli.journal.lessons import recent_lessons
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.sentry.adopt import adopt_unmanaged
from hlcli.sentry.apply import manage_open_trades
from hlcli.sentry.engine import active as trail_active
from hlcli.state.store import DeferredCandidate, StateStore

DecideFn = Callable[..., DecisionResult]

# Scheduling bounds for a WAIT re-check (the runner clamps the LLM's request into these).
_MIN_RECHECK_SECONDS = 60.0  # don't re-check sooner than this — avoids hot-looping a pass
_DEFAULT_RECHECK_MINUTES = 5.0  # used when the model gives no usable recheck time


class PassSummary(BaseModel):
    network: Network
    seen: int
    rechecked: int
    approved: int   # cleared the gate (fired, shadow-logged, or failed downstream)
    fired: int
    rejected: int   # the gate said no
    failed: int     # gate-approved but died at the exchange (reject/unfilled/aborted)
    dropped: int
    deferred: int
    resolved: int
    managed: int = 0  # sentry 6a actions applied (stops moved + scale-outs)
    note: str


def run_once(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    dry_run: bool = False,
    fire_enabled: bool = True,
    decide_fn: DecideFn | None = None,
    alerter: Alerter | None = None,
    now: float | None = None,
    include_intake: bool = True,
) -> PassSummary:
    """One pass. `include_intake=False` is the sentry watch pass (PLAN.md §14): manage
    open trades, resolve, and re-check due WAIT deferrals on sentry's cadence — but
    never consume the intake stream, which remains `exec`'s job. Both paths share the
    deferred table and idempotency keys, so running them side by side can't
    double-spend a follow-up attempt or double-fire an entry."""
    now = now if now is not None else time.time()
    # Arbiter selection is a hard cap (HL_DECISION_SOURCE): resolving it here wires every
    # caller — exec/sentry/agent — through one switch; tests still inject their own.
    decide_fn = decide_fn if decide_fn is not None else decider_for(caps)
    breaker = Breaker(state, caps)
    protected = requires_native_protection(exchange.network)

    marks = exchange.get_marks()
    # Sentry 6a: ratchet stops / bank scale-outs BEFORE resolving, so this pass's
    # close-out check runs against the tightened levels. The engine guarantees a
    # moved stop sits strictly on the losing side of the current mark, so a tighten
    # can never trigger its own close in the same pass. All-off config ⇒ no-op.
    managed = 0
    if not dry_run and trail_active(tunable.trail):
        m = manage_open_trades(exchange, state, tunable, now, marks=marks,
                               native_protected=protected, shadow_only=not fire_enabled,
                               alerter=alerter)
        managed = m.stops_moved + m.scaled_out

    # Monitor step: close any open trade whose SL/TP/expiry has triggered, recording
    # its outcome. A shadow pass resolves only its hypothetical trades (orderlessly —
    # that's what turns shadow decisions into tuner/graduation outcomes); dry-run
    # resolves nothing.
    resolved = (
        resolve_open_trades(exchange, state, caps, tunable, now, marks=marks,
                            native_protected=protected, shadow_only=not fire_enabled)
        if not dry_run else 0
    )

    equity = exchange.equity()
    positions = exchange.get_positions()
    open_coins = {p.coin for p in positions}
    # Account-wide exposure at pass start (audit A), priced at the mark; a position whose mark
    # is momentarily missing falls back to its entry price so a dropped quote can't undercount
    # the book (fail-closed — an undercount would loosen the cap).
    gross_notional = sum(abs(p.size) * (marks.get(p.coin) or p.entry_price) for p in positions)
    if not fire_enabled:
        # Shadow's book is hypothetical — feed its open trades into one-per-coin /
        # max-concurrent (and gross exposure) so shadow discipline matches what live would do.
        shadow_open = state.open_trades(shadow=True)
        open_coins |= {t["coin"] for t in shadow_open}
        gross_notional += sum(abs(t["size"]) * (marks.get(t["coin"]) or t["entry"]) for t in shadow_open)
    # New entries opened this UTC day (audit B); grows as this pass fires so a burst can't blow
    # the budget. `now` is unix (UTC-epoch) so `now % 86400` is the offset into the day.
    trades_today = state.count_trades_opened_since(now - (now % 86400.0), shadow=not fire_enabled)
    realized = state.paper_realized() if exchange.network is Network.PAPER else None
    recent = state.recent_decisions(limit=10)
    outcomes = state.resolved_trades(limit=10)  # newest first — the model's track record
    breaker_tripped = breaker.tripped()
    daily_loss = breaker.daily_loss_hit(equity, persist=not dry_run)
    # Reconciliation (O-2): the unmanaged-position check runs on EVERY non-dry pass —
    # shadow included — so drift between the exchange and the ledger is never silent.
    # The auto-response (adopt) needs a live-authorized pass: it writes real ledger
    # rows, which is never a shadow pass's job. Adoption first, so positions it books
    # drop out of the unmanaged alert; stopless ones remain and keep paging.
    if not dry_run:
        if caps.reconcile_action == "adopt" and fire_enabled:
            adopt_unmanaged(exchange, state, positions=positions, alerter=alerter, now=now)
        if alerter is not None:
            _alert_unmanaged(state, alerter, positions)

    # WAIT re-checks (skip in a dry-run preview, and while the kill switch is tripped — a
    # re-check can't fire anyway, so don't spend an LLM call or a follow-up attempt on it;
    # parked candidates wait, attempts intact, until the breaker clears).
    due = state.due_deferred(now) if (not dry_run and not breaker_tripped) else []
    batch = state.pull_new(limit=tunable.max_candidates_per_pass) if include_intake else []
    if alerter is not None and not dry_run:
        _alert_halt(state, alerter, batch, breaker_tripped, daily_loss)

    coins = {c.coin for _, c in batch} | {d.candidate.coin for d in due}
    common = _PassContext(
        caps=caps, tunable=tunable, decide_fn=decide_fn, marks=marks,
        market=_market_context(exchange, coins), equity=equity, positions=positions,
        realized=realized, recent=recent, outcomes=outcomes,
        lessons=recent_lessons(state, caps, tunable), open_coins=open_coins,
        gross_notional=gross_notional, trades_today=trades_today, now=now,
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
        approved=t.approved, fired=t.fired, rejected=t.rejected, failed=t.failed,
        dropped=t.dropped, deferred=t.deferred, resolved=resolved, managed=managed,
        note=_note(dry_run=dry_run, fire_enabled=fire_enabled),
    )


@dataclass
class _Tally:
    approved: int = 0
    fired: int = 0
    rejected: int = 0
    failed: int = 0  # gate-approved but died at the exchange — disjoint from `rejected`
    dropped: int = 0
    deferred: int = 0


@dataclass
class _PassContext:
    """Everything one pass shares across candidates. `open_coins`, `gross_notional`,
    `trades_today` and `tally` are mutated as candidates are processed; the rest is
    read-only per-pass state."""

    caps: Caps
    tunable: TunableConfig
    decide_fn: DecideFn
    marks: dict[str, float]
    market: dict[str, tuple]  # coin → (labeled candle context, regime); see _market_context
    equity: float
    positions: list[Position]
    realized: float | None
    recent: list[dict]
    outcomes: list[dict]  # recently resolved trades — the model's track record
    lessons: list[dict]  # distilled daily lessons (§15.4); [] when the inject is off
    open_coins: set[str]
    gross_notional: float  # open-book notional (mark-priced); grows as this pass fires
    trades_today: int  # new entries opened this UTC day; grows as this pass fires
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
    # A concurrent pass (e.g. `exec run` beside `sentry run`) may have already fired
    # this candidate — the atomic fire-claim blocks a double-fire, but re-deciding it
    # would still burn an LLM call and could re-park a candidate that already fired.
    if state.already_fired(d.candidate.id):
        state.drop_deferred(d.candidate.id)
        return
    attempts_left = d.attempts_remaining - 1
    expires_in = (d.candidate.created_at + common.caps.max_signal_age_minutes * 60 - common.now) / 60
    followup = {"attempts_remaining": attempts_left, "expires_in_minutes": round(max(0.0, expires_in), 1)}
    step = _evaluate(exchange, state, d.candidate, common, attempts_left=attempts_left, followup=followup)
    if step.status == "deferred":
        state.defer_candidate(d.candidate, step.next_check_at, step.attempts_remaining)
    else:
        state.drop_deferred(d.candidate.id)


def _evaluate(exchange: Exchange, state: StateStore, candidate: Candidate, common: _PassContext, *,
              attempts_left: int, followup: dict | None = None) -> _Step:
    """enrich → decide → (drop | WAIT-defer | gate → fire) → log, tallying as it goes.
    Returns a `_Step` describing the outcome; the caller owns intake/deferred persistence.
    `followup` marks a WAIT re-check so the model knows this isn't a fresh look."""
    candles, regime = common.market[candidate.coin]  # built from this pass's batch ∪ due coins

    # No mark ⇒ the gate's mark-sanity check would reject regardless of the verdict,
    # so don't spend a paid LLM call to find that out.
    if common.marks.get(candidate.coin) is None:
        common.tally.rejected += 1
        if not common.dry_run:
            state.log_decision(candidate.id, common.now, context={
                "coin": candidate.coin, "outcome": "rejected", "rejected": "no mark for coin"})
            _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason="no mark for coin")
        return _Step("rejected")

    # Advisory injection screen on the human-supplied thesis (L-5): flagged candidates
    # still flow to the decision + gate, but the flags are alerted and logged so a
    # poisoned intake feed is visible in the audit trail, never silent.
    flags = injection_flags(candidate)
    if flags and not common.dry_run:
        _emit(common.alerter, "thesis_flagged", level="warning", coin=candidate.coin,
              candidate=candidate.id, flags=flags)

    ctx = enrich(
        candidate, marks=common.marks, equity=common.equity, positions=common.positions,
        realized=common.realized, recent=common.recent, outcomes=common.outcomes,
        tunable=common.tunable, candles=candles, regime=regime, lessons=common.lessons,
        followup=followup, now=common.now,
    )
    result = common.decide_fn(ctx, common.caps, common.tunable)

    if result.dropped:
        common.tally.dropped += 1
        if not common.dry_run:
            state.log_decision(candidate.id, common.now, context={
                "coin": candidate.coin, "outcome": "dropped", "dropped": result.note,
                "raw": result.raw, "stop_reason": result.stop_reason,
            })
        return _Step("dropped")

    decision = result.decision

    # Intercept WAIT before the gate: park it for a fresh re-check instead of rejecting.
    if decision.action is Action.ACT and decision.timing is Timing.WAIT:
        return _wait(state, candidate, decision, regime, common, attempts_left)

    gate_ctx = GateContext(
        caps=common.caps, tunable=common.tunable, equity=common.equity,
        open_coins=set(common.open_coins), open_count=len(common.open_coins),
        now=common.now, breaker_tripped=common.breaker_tripped, daily_loss_hit=common.daily_loss,
        regime=regime, mark=common.marks.get(candidate.coin), gross_notional=common.gross_notional,
        trades_today=common.trades_today,
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
        status = "shadow"  # approved but deliberately not fired — booked hypothetically
        _open_shadow_trade(state, candidate, decision, outcome, regime, common)
    else:
        common.tally.approved += 1
        status, fill = _fire_and_reconcile(exchange, state, candidate, decision, outcome, regime, common)

    context = {"coin": candidate.coin, "outcome": status, "equity": common.equity,
               "open_coins": sorted(common.open_coins), "regime": regime,
               # which reflection rows were in the model's context — makes the
               # inject's value measurable (§15.4), like 6b measured the manager
               "lessons": [le["date"] for le in common.lessons]}
    if flags:
        context["thesis_flags"] = flags  # the injection screen's audit-trail record
    state.log_decision(candidate.id, common.now, decision=decision, gate=outcome, fill=fill,
                       context=context)
    return _Step(status)


def _fire_and_reconcile(exchange, state, candidate, decision, outcome, regime, common: _PassContext) -> tuple[str, OrderResult]:
    """Fire an approved order and reconcile against what actually filled — not what we
    intended. The ledger row is written the moment the fill is confirmed — *before*
    protection — so a crash mid-protection leaves a position the resolver still
    manages, never an untracked one. An abort then resolves that row rather than
    deleting history."""
    fill = fire(exchange, state, candidate, outcome.order, common.now)
    filled = fill.filled_size if fill.filled_size is not None else outcome.order.size
    entry_price = fill.avg_price if fill.avg_price is not None else candidate.entry
    if not fill.accepted:
        common.tally.failed += 1  # duplicate or exchange reject
        _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason=fill.message or fill.status)
        return "rejected", fill
    if filled <= 0:
        common.tally.failed += 1  # accepted but nothing filled (rested/canceled) — no position
        _emit(common.alerter, "reject", level="warning", coin=candidate.coin, reason="unfilled")
        return "unfilled", fill

    trade_id = state.open_trade(
        candidate.id, candidate.coin, candidate.side, entry_price,
        candidate.sl, candidate.tp, filled, decision.conviction, regime, common.now,
        mark_at_entry=common.marks.get(candidate.coin),  # entry_price − mark = realized slip (audit D)
    )
    if common.protected:
        secured = _secure(exchange, candidate, filled, common.alerter)
        if not secured.protected:
            common.tally.failed += 1
            if secured.close_confirmed:  # flattened cleanly — book the ~spread cost as an abort
                close = secured.close
                exit_price = close.avg_price if close.avg_price is not None else entry_price
                realized, r_multiple = _abort_pnl(candidate.side, entry_price, candidate.sl, exit_price, filled)
                state.resolve_trade(trade_id, "aborted", exit_price, realized, r_multiple, common.now)
                return "aborted", fill
            # Flatten unconfirmed: the position may still be live and unprotected. Record it
            # honestly (no fabricated P&L) under a distinct terminal status; the critical
            # alert already fired, and next pass's _alert_unmanaged re-flags the live position.
            state.resolve_trade(trade_id, "abort_failed", entry_price, 0.0, 0.0, common.now)
            return "abort_failed", fill
        # Record the triggers so sentry (and the resolver) later cancel this position's
        # protection by oid, never a sibling slice's.
        state.update_trade_triggers(trade_id, sl_oid=secured.sl_oid, tp_oid=secured.tp_oid)
    common.tally.fired += 1
    common.open_coins.add(candidate.coin)
    common.gross_notional += filled * entry_price  # so later candidates this pass see the new exposure
    common.trades_today += 1
    _emit(common.alerter, "fire", level="info", coin=candidate.coin, side=candidate.side.value,
          size=filled, conviction=decision.conviction, order_id=fill.order_id)
    return "fired", fill


def _open_shadow_trade(state: StateStore, candidate: Candidate, decision, outcome, regime, common: _PassContext) -> None:
    """Book a gate-approved shadow decision as a hypothetical trade, entered at the
    mark (what a MARKET fill would have paid). Resolved orderlessly by later shadow
    passes — this is how shadow accumulates the outcomes the tuner and the
    graduation checklist need before any real money moves."""
    entry = common.marks[candidate.coin]  # gate approval guarantees the mark exists
    state.open_trade(
        candidate.id, candidate.coin, candidate.side, entry,
        candidate.sl, candidate.tp, outcome.size, decision.conviction, regime, common.now,
        shadow=True,
    )
    common.open_coins.add(candidate.coin)  # the hypothetical book honors one-per-coin too
    common.gross_notional += outcome.notional  # …and the account-wide exposure cap
    common.trades_today += 1                    # …and the daily new-entry budget


def _abort_pnl(side: Side, entry: float, sl: float, exit_price: float, size: float) -> tuple[float, float]:
    """Realized P&L / R-multiple of an emergency-closed entry (usually ≈ the spread)."""
    per_unit = (exit_price - entry) if side is Side.LONG else (entry - exit_price)
    risk = abs(entry - sl)
    return round(per_unit * size, 6), round(per_unit / risk, 4) if risk > 0 else 0.0


def _wait(state: StateStore, candidate: Candidate, decision, regime, common: _PassContext, attempts_left: int) -> _Step:
    """Defer an act+wait verdict for a later re-check — as long as an attempt remains and
    there's freshness room left; otherwise it's a terminal reject (like the gate's WAIT path)."""
    next_at = _schedule_recheck(candidate, decision.recheck_in_minutes, common.now, common.caps) if attempts_left >= 1 else None
    if next_at is None:
        common.tally.rejected += 1
        reason = "wait: out of attempts" if attempts_left < 1 else "wait: would be stale before re-check"
        if not common.dry_run:
            state.log_decision(candidate.id, common.now, decision=decision,
                               context={"coin": candidate.coin, "outcome": "rejected",
                                        "wait": reason, "regime": regime})
        return _Step("rejected")
    common.tally.deferred += 1
    if not common.dry_run:
        state.log_decision(
            candidate.id, common.now, decision=decision,
            context={"coin": candidate.coin, "outcome": "deferred", "wait": "deferred",
                     "next_check_at": next_at, "attempts_remaining": attempts_left, "regime": regime},
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
    # The candle tail is fetched at DECISION_INTERVAL and labeled with it in the prompt
    # payload — bare OHLC bars are meaningless to the model without their timeframe.
    bars = _fetch_candles(exchange, coin)
    tail = summarize(bars)
    candles = {"interval": DECISION_INTERVAL, "order": "oldest_first", "bars": tail} if tail else None
    return candles, classify(bars)


def _fetch_candles(exchange: Exchange, coin: str):
    # Best-effort: candles are decision *context*, not a safety input, so a feed hiccup
    # degrades this coin to "no context" rather than aborting a pass that already has
    # valid marks. (A marks failure still aborts — that read is load-bearing.)
    # Catch only genuine feed failures — network/HTTP errors and a malformed or
    # unexpected response shape; an unexpected bug (e.g. a programming error) still
    # surfaces rather than masquerading as "no candles".
    try:
        return exchange.get_candles(coin, interval=DECISION_INTERVAL)
    except (httpx.HTTPError, KeyError, ValueError, TypeError):
        return []


@dataclass
class _SecureOutcome:
    """What `_secure` did with a filled entry. `protected` True ⇒ native triggers rest
    (oids set). Otherwise the entry was flattened; `close_confirmed` says whether that
    flatten actually filled — an *unconfirmed* flatten may have left the position live
    and unprotected, which the caller must not record as a clean abort."""

    protected: bool
    sl_oid: str | None = None
    tp_oid: str | None = None
    close: OrderResult | None = None       # emergency-close result when not protected
    close_confirmed: bool = False          # True only when the flatten actually filled


def _secure(exchange: Exchange, candidate, size: float, alerter: Alerter | None) -> _SecureOutcome:
    """Place native protective triggers; if that fails, flatten the position rather than
    leave it naked — and cancel whichever trigger DID place, so no stray reduce-only order
    survives to ambush the next position.

    The flatten must be *confirmed*: an emergency close that errors or doesn't fill leaves
    a live, unprotected position, so the caller must not book it as a clean abort. A raised
    backend error is caught (never propagated) so a naked position is always recorded and
    alerted, not turned into a crash that skips the ledger update entirely."""
    protection = place_protection(exchange, candidate, size)
    if protection.ok:
        sl, tp = protection.placed  # place_protection orders SL then TP
        return _SecureOutcome(protected=True, sl_oid=sl.order_id, tp_oid=tp.order_id)

    closed = _emergency_close_confirmed(exchange, candidate, size)
    confirmed = closed.accepted and (closed.filled_size or 0) > 0
    canceled = cancel_placed(exchange, candidate.coin, protection.placed)
    _emit(alerter, "protection_failed", level="critical", coin=candidate.coin,
          reason=protection.failed, emergency_closed=confirmed, triggers_canceled=canceled)
    if not confirmed:
        # The position may still be open and unprotected — page loudly and distinctly so a
        # human intervenes now, ahead of next pass's _alert_unmanaged backstop.
        _emit(alerter, "emergency_close_failed", level="critical", coin=candidate.coin,
              size=size, reason=closed.message or closed.status)
    return _SecureOutcome(protected=False, close=closed, close_confirmed=confirmed)


def _emergency_close_confirmed(exchange: Exchange, candidate, size: float) -> OrderResult:
    """`emergency_close`, but a raised backend error becomes a definitive non-fill result
    instead of propagating — a naked position must be *recorded*, never crash the pass. The
    broad catch is deliberate: the sole caller immediately alerts critical on a non-fill, so
    nothing is masked silently, and any error here means the flatten is unconfirmed."""
    try:
        return emergency_close(exchange, candidate, size)
    except Exception as exc:  # noqa: BLE001 — money-safety net (see docstring)
        return OrderResult(accepted=False, status="error", message=f"emergency close raised: {exc}")


def _emit(alerter: Alerter | None, event: str, **fields) -> None:
    if alerter is not None:
        alerter.alert(event, **fields)


_HALT_ALERT_KEY = "alert_halt_last"


def _alert_halt(state: StateStore, alerter: Alerter, batch, breaker_tripped: bool, daily_loss: bool) -> None:
    """Alert when the breaker / loss-limit is blocking candidates — but only on a
    *change* of state, so a tripped breaker doesn't spam the run loop every pass.
    Parked WAIT deferrals count as blocked work too — they're frozen while halted."""
    reason = "kill switch" if breaker_tripped else "daily loss limit" if daily_loss else ""
    blocked = len(batch) + state.deferred_count()
    if reason and blocked:
        if state.meta_get(_HALT_ALERT_KEY) != reason:
            alerter.alert("halted", level="critical", reason=reason, candidates=blocked)
            state.meta_set(_HALT_ALERT_KEY, reason)
    elif not reason and state.meta_get(_HALT_ALERT_KEY):
        state.meta_set(_HALT_ALERT_KEY, "")  # cleared — re-arm for the next trip


_UNMANAGED_ALERT_KEY = "alert_unmanaged_last"


def _alert_unmanaged(state: StateStore, alerter: Alerter, positions: list[Position]) -> None:
    """Alert (on change) when the exchange holds a position the ledger doesn't know —
    e.g. a crash between fill and ledger write, or a manual trade on the executor's
    account. The resolver won't manage it, so a human has to.

    Only *real* ledger rows count as managed: a shadow (hypothetical) trade on the
    same coin claims nothing on the exchange and must not mask a live orphan."""
    ledger_coins = {t["coin"] for t in state.open_trades(shadow=False)}
    unmanaged = sorted(p.coin for p in positions if p.coin not in ledger_coins)
    fingerprint = ",".join(unmanaged)
    if fingerprint != (state.meta_get(_UNMANAGED_ALERT_KEY) or ""):
        if unmanaged:
            alerter.alert("unmanaged_position", level="critical", coins=unmanaged)
        state.meta_set(_UNMANAGED_ALERT_KEY, fingerprint)


def _note(*, dry_run: bool, fire_enabled: bool) -> str:
    if dry_run:
        return "dry-run (no state changes)"
    if not fire_enabled:
        return "shadow (logged, fired nothing)"
    return "ok"
