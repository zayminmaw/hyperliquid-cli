"""Order + position reconciliation (wave-2 G).

A single structured diff of the exchange (positions + resting orders) against the
executor's ledger, yielding a `safe` / `requires_halt` verdict. hl already reconciles
*piecemeal* during a pass — `resolve.py` books vanished positions, `runner._alert_unmanaged`
flags orphans — but nothing forces a **halt** when the book and the ledger disagree in a way
that makes firing unsafe. On a restart (especially mainnet, after a crash), that verdict is
the backstop: reconcile before firing, and trip the breaker if the book is not what the
ledger says.

Divergences that require a halt:
  - `unexpected_position` — the exchange holds a position with no real ledger row;
  - `size_mismatch`      — same coin, but exchange size ≠ ledger size beyond tolerance
                           (a partial liquidation / partial fill the ledger never saw);
  - `unprotected_position` — a live position with no native reduce-only trigger resting
                           (checked only where native protection is required — testnet/mainnet;
                           paper protects via the resolver, so it is skipped there).

A ledger row with **no** exchange position is *not* a divergence here — the resolver books
it as a vanished/closed trade. G is the dangerous direction: exchange reality the ledger
can't account for.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlcli.exchange.base import Exchange
from hlcli.executor.protect import requires_native_protection
from hlcli.state.store import StateStore

# Relative size tolerance — absorbs wire-rounding on the exchange's reported size while still
# flagging a real partial close (which moves size by far more than a percent).
_SIZE_TOLERANCE = 0.01


@dataclass(frozen=True)
class Divergence:
    kind: str  # unexpected_position | size_mismatch | unprotected_position
    coin: str
    detail: dict


@dataclass(frozen=True)
class ReconcileReport:
    divergences: list[Divergence]

    @property
    def is_safe(self) -> bool:
        return not self.divergences

    @property
    def requires_halt(self) -> bool:
        # Every divergence kind here makes firing unsafe until a human reconciles.
        return bool(self.divergences)


def reconcile(exchange: Exchange, state: StateStore) -> ReconcileReport:
    """Diff the live book against the real (non-shadow) ledger. Pure read; no writes."""
    positions = {p.coin: p for p in exchange.get_positions()}
    protected = {o.coin for o in exchange.get_open_orders() if o.is_trigger and o.reduce_only}
    ledger = {t["coin"]: t for t in state.open_trades(shadow=False)}
    check_protection = requires_native_protection(exchange.network)

    divergences: list[Divergence] = []
    for coin, p in sorted(positions.items()):
        trade = ledger.get(coin)
        if trade is None:
            divergences.append(Divergence("unexpected_position", coin, {"size": p.size}))
            continue
        if abs(p.size - trade["size"]) > _SIZE_TOLERANCE * trade["size"]:
            divergences.append(Divergence(
                "size_mismatch", coin, {"ledger": trade["size"], "exchange": p.size}))
        if check_protection and coin not in protected:
            divergences.append(Divergence("unprotected_position", coin, {"size": p.size}))
    return ReconcileReport(divergences)
