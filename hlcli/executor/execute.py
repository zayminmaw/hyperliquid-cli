"""Firing approved orders, idempotently (PLAN.md §5).

The idempotency key is the candidate id, recorded **before** the order is sent.
A crash between record and fill therefore skips (a missed trade) rather than
double-fires (duplicate risk) — the safer failure. Combined with the intake
high-water mark, a restart never re-fires a candidate.

A *definitive* reject (the backend returned, refusing the order) releases the key:
nothing reached the book, so the key store shouldn't claim the candidate fired.

A *transport-unknown* submit (the call raised, so we don't know if the entry
reached the book) is resolved by the order's **client id (cloid)** where the backend
supports it: a real fill is returned so the pass tracks and protects the position,
and an order the exchange never saw releases the key. Only when a backend can't
resolve by cloid do we fall back to raising — the outcome stays unknown, the key is
kept, and the candidate is treated as spent rather than risk a double-fire.
"""

from __future__ import annotations

import hashlib

from hlcli.core.types import Candidate, Order, OrderResult
from hlcli.exchange.base import Exchange
from hlcli.state.store import StateStore


def entry_cloid(candidate_id: str) -> str:
    """Deterministic 16-byte client order id for a candidate's entry ("0x" + 32 hex).
    Deterministic so a resubmit or a status lookup addresses the same order."""
    return "0x" + hashlib.sha256(candidate_id.encode()).digest()[:16].hex()


def fire(exchange: Exchange, state: StateStore, candidate: Candidate, order: Order, when: float) -> OrderResult:
    # One atomic claim marks intent AND detects a duplicate: two passes racing on the
    # same candidate (e.g. `exec run` beside `sentry run`) can't both win the insert.
    if not state.record_fire(candidate.id, None, when):
        return OrderResult(accepted=False, status="duplicate", message="already fired (idempotent skip)")
    order = order.model_copy(update={"cloid": entry_cloid(candidate.id)})
    try:
        result = exchange.place_order(order)
    except Exception as exc:  # noqa: BLE001 — transport-unknown; resolve by cloid, never guess
        result = _resolve_unknown(exchange, order, exc)
    if not result.accepted:
        state.release_fire(candidate.id)  # nothing on the book — don't leave a false "fired" mark
    return result


def _resolve_unknown(exchange: Exchange, order: Order, exc: Exception) -> OrderResult:
    """The submit raised, so the entry may or may not have reached the book. Resolve it by
    the order's cloid where the backend supports the lookup: a real fill is returned so the
    caller opens the ledger + places protection; an order the exchange never saw is a clean
    non-placement (accepted=False → key released, no double-fire). A backend without cloid
    lookup — or a lookup that itself fails — re-raises so the key stays claimed (spent)
    rather than risking a second fire; the orphan is then caught by `_alert_unmanaged`."""
    lookup = getattr(exchange, "order_status_by_cloid", None)
    if lookup is None or order.cloid is None:
        raise exc
    status = lookup(order.cloid)  # a raise here propagates — keeping the key is the safe failure
    if status is None:
        return OrderResult(accepted=False, status="unresolved", message=f"submit failed, not on book: {exc}")
    if status.status == "resting":
        # An IOC entry must not rest — don't leave a surprise order live on the book.
        # Cancel it (a raise here propagates → key kept), then report a clean
        # non-placement so the caller releases the key.
        if status.order_id is None:
            raise exc
        exchange.cancel(order.coin, int(status.order_id))
        return OrderResult(accepted=False, status="unresolved",
                           message=f"resting entry canceled after transport error: {exc}")
    return status
