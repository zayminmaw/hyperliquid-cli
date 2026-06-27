"""Firing approved orders, idempotently (PLAN.md §5).

The idempotency key is the candidate id, recorded **before** the order is sent.
A crash between record and fill therefore skips (a missed trade) rather than
double-fires (duplicate risk) — the safer failure. Combined with the intake
high-water mark, a restart never re-fires a candidate.
"""

from __future__ import annotations

from hlcli.core.types import Candidate, Order, OrderResult
from hlcli.exchange.base import Exchange
from hlcli.state.store import StateStore


def fire(exchange: Exchange, state: StateStore, candidate: Candidate, order: Order, when: float) -> OrderResult:
    if state.already_fired(candidate.id):
        return OrderResult(accepted=False, status="duplicate", message="already fired (idempotent skip)")

    state.record_fire(candidate.id, None, when)  # mark intent first
    return exchange.place_order(order)
