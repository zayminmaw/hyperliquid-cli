"""The executor pass (PLAN.md §5).

One pass: resolve positions/equity → pull new candidates past the high-water mark
→ deterministic decision → risk gate → fire approved (idempotent) → log → advance
the HWM. `dry_run` computes everything but mutates no state (a side-effect-free
preview). LLM enrich/decision arrive in Phase 3; the gate is unchanged by them.
"""

from __future__ import annotations

import time

from pydantic import BaseModel

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.executor.decision import decide
from hlcli.executor.execute import fire
from hlcli.executor.gate import GateContext, evaluate
from hlcli.safety.breaker import Breaker
from hlcli.state.store import StateStore


class PassSummary(BaseModel):
    network: Network
    seen: int
    approved: int
    fired: int
    rejected: int
    note: str


def run_once(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    dry_run: bool = False,
    now: float | None = None,
) -> PassSummary:
    now = now if now is not None else time.time()
    breaker = Breaker(state, caps)

    equity = exchange.equity()
    open_coins = {p.coin for p in exchange.get_positions()}
    breaker_tripped = breaker.tripped()
    daily_loss = breaker.daily_loss_hit(equity)

    batch = state.pull_new(limit=tunable.max_candidates_per_pass)
    approved = fired = rejected = 0

    for seq, candidate in batch:
        decision = decide(candidate)
        ctx = GateContext(
            caps=caps, tunable=tunable, equity=equity,
            open_coins=set(open_coins), open_count=len(open_coins),
            now=now, breaker_tripped=breaker_tripped, daily_loss_hit=daily_loss,
        )
        outcome = evaluate(candidate, decision, ctx)

        if dry_run:
            approved += int(outcome.approved)
            rejected += int(not outcome.approved)
            continue  # side-effect free

        fill = None
        status = "rejected"
        if outcome.approved:
            approved += 1
            fill = fire(exchange, state, candidate, outcome.order, now)
            if fill.accepted:
                fired += 1
                open_coins.add(candidate.coin)
                status = "fired"
            else:
                rejected += 1  # duplicate or exchange reject
        else:
            rejected += 1

        state.log_decision(
            candidate.id, now, decision=decision, gate=outcome, fill=fill,
            context={"equity": equity, "open_coins": sorted(open_coins)},
        )
        state.set_status(seq, status)
        state.advance_hwm(seq)

    return PassSummary(
        network=exchange.network, seen=len(batch), approved=approved,
        fired=fired, rejected=rejected,
        note="dry-run (no state changes)" if dry_run else "ok",
    )
