"""Firing approved orders, idempotently (PLAN.md §5).

The idempotency key is the candidate id, recorded **before** the order is sent.
A crash between record and fill therefore skips (a missed trade) rather than
double-fires (duplicate risk) — the safer failure. Combined with the intake
high-water mark, a restart never re-fires a candidate.

A *definitive* reject (the backend returned, refusing the order) releases the key:
nothing reached the book, so the key store shouldn't claim the candidate fired. A
transport error raises instead — the outcome is unknown, so the key is kept and the
candidate is treated as spent rather than risk a double-fire.
"""

from __future__ import annotations

from hlcli.core.types import Candidate, Order, OrderResult
from hlcli.exchange.base import Exchange
from hlcli.state.store import StateStore


def fire(exchange: Exchange, state: StateStore, candidate: Candidate, order: Order, when: float) -> OrderResult:
    # One atomic claim marks intent AND detects a duplicate: two passes racing on the
    # same candidate (e.g. `exec run` beside `sentry run`) can't both win the insert.
    if not state.record_fire(candidate.id, None, when):
        return OrderResult(accepted=False, status="duplicate", message="already fired (idempotent skip)")
    result = exchange.place_order(order)
    if not result.accepted:
        state.release_fire(candidate.id)  # nothing filled — don't leave a false "fired" mark
    return result
