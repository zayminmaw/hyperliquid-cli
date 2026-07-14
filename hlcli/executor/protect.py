"""Native exchange-side SL/TP — the mainnet prerequisite (PLAN.md §7).

On a live backend the executor must not be the *only* thing between an open
position and a runaway loss: a crashed process would leave the position naked. So
at entry time we place protective reduce-only trigger orders **on the exchange** —
a stop-loss and a take-profit at the candidate's levels — which the exchange
honours even if this process dies. This reuses the Mode-A `stop-loss`/`take-profit`
trigger path the live backend already exposes.

Paper has no real book to protect, so there the Phase-4 resolver stays the monitor.
The split is `requires_native_protection(network)`: True for testnet + mainnet.

The hard prerequisite has teeth in the runner: if protection cannot be placed after
an entry fills, the position is emergency-closed rather than left unprotected.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hlcli.core.backoff import backoff_delay
from hlcli.core.types import Candidate, Network, Order, OrderResult, OrderType, Side
from hlcli.exchange.base import Exchange

# Bounded retry for the reduce-only order-writes (protection + emergency close). Kept small:
# a live position is unprotected while we retry, so a couple of quick attempts covers a
# transient blip / rate-limit without stalling the flatten. `_sleep` is a module attribute
# so tests can neutralize the backoff wait.
_RETRY_ATTEMPTS = 3
_RETRY_BASE = 0.5
_RETRY_MAX = 3.0
_sleep = time.sleep


def place_reduce_only(exchange: Exchange, order: Order) -> OrderResult:
    """Place a reduce-only order, retrying transport / rate-limit failures with backoff.

    A duplicate reduce-only order is idempotency-safe — it can only reduce, never flip or
    open — so a transport-unknown outcome is safe to re-send (unlike an entry, which needs
    a cloid). A raised backend error is retried and, if it persists, returned as a
    definitive non-placement (`accepted=False`) so the caller flattens/aborts instead of
    crashing the pass. A definitive reject (the backend answered "no") is returned
    immediately and never retried."""
    last = "no attempt"
    for failures in range(_RETRY_ATTEMPTS):
        try:
            return exchange.place_order(order)  # OrderResult (incl. a real reject) → done
        except Exception as exc:  # noqa: BLE001 — transport/rate-limit; reduce-only is safe to retry
            last = str(exc)
            if failures < _RETRY_ATTEMPTS - 1:
                _sleep(backoff_delay(_RETRY_BASE, failures + 1, _RETRY_MAX))
    return OrderResult(accepted=False, status="error", message=f"exhausted retries: {last}")


def requires_native_protection(network: Network) -> bool:
    """Live backends place native triggers; paper relies on the executor-side resolver."""
    return network is not Network.PAPER


def _closing_side(side: Side) -> Side:
    return Side.SHORT if side is Side.LONG else Side.LONG


def protective_orders(candidate: Candidate, size: float) -> list[Order]:
    """The reduce-only stop-loss + take-profit that protect a filled entry."""
    closing = _closing_side(candidate.side)
    return [
        Order(coin=candidate.coin, side=closing, order_type=OrderType.STOP_LOSS,
              size=size, trigger_price=candidate.sl, reduce_only=True),
        Order(coin=candidate.coin, side=closing, order_type=OrderType.TAKE_PROFIT,
              size=size, trigger_price=candidate.tp, reduce_only=True),
    ]


@dataclass
class ProtectionResult:
    ok: bool  # True only if BOTH protective triggers were accepted
    placed: list[OrderResult] = field(default_factory=list)
    failed: str = ""  # first failure message, when not ok


def place_protection(exchange: Exchange, candidate: Candidate, size: float) -> ProtectionResult:
    """Place both protective triggers; stop at the first rejection so we don't half-protect."""
    placed: list[OrderResult] = []
    for order in protective_orders(candidate, size):
        result = place_reduce_only(exchange, order)
        placed.append(result)
        if not result.accepted:
            return ProtectionResult(ok=False, placed=placed, failed=result.message or result.status)
    return ProtectionResult(ok=True, placed=placed)


def emergency_close(exchange: Exchange, candidate: Candidate, size: float) -> OrderResult:
    """Flatten a just-opened position whose protection could not be placed. Reduce-only,
    so the retry inside `place_reduce_only` is safe even on a transport-unknown outcome."""
    return place_reduce_only(exchange, Order(
        coin=candidate.coin, side=_closing_side(candidate.side),
        order_type=OrderType.MARKET, size=size, reduce_only=True,
    ))


def cancel_placed(exchange: Exchange, coin: str, placed: list[OrderResult]) -> int:
    """Cancel triggers that DID place during a failed protection attempt — a stray
    reduce-only trigger left resting can close the *next* position in this coin.
    Best-effort: a cancel that fails is not worth failing the abort path over."""
    canceled = 0
    for result in placed:
        if result.accepted and result.order_id and result.order_id.isdigit():
            canceled += int(exchange.cancel(coin, int(result.order_id)).accepted)
    return canceled


def cancel_coin_triggers(exchange: Exchange, coin: str) -> int:
    """Cancel every resting reduce-only trigger for `coin` — the last-resort sweep when
    a coin has no open ledger row left, so the surviving half of an SL/TP pair can't
    ambush a future position. Never call this while a sibling slice is still open (it
    would strip that slice's protection); use `cancel_trade_triggers` for one row."""
    canceled = 0
    for order in exchange.get_open_orders():
        if order.coin == coin and order.is_trigger and order.reduce_only:
            canceled += int(exchange.cancel(coin, order.oid).accepted)
    return canceled


def cancel_trade_triggers(exchange: Exchange, trade: dict) -> int:
    """Cancel only the native SL/TP triggers this ledger row placed (by recorded oid),
    leaving any sibling slice's protection intact — the slice-scoped cancel a coin that
    has been added to (§14 ADD) requires. A row with no recorded oids cancels nothing."""
    canceled = 0
    for oid in (trade.get("sl_oid"), trade.get("tp_oid")):
        if oid and str(oid).isdigit():
            canceled += int(exchange.cancel(trade["coin"], int(oid)).accepted)
    return canceled
