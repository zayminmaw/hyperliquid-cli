"""Per-asset tick/size rounding for live orders (PLAN.md: code owns the rounding).

Hyperliquid rejects orders whose size doesn't respect the asset's `szDecimals`
or whose price exceeds 5 significant figures / `6 − szDecimals` decimal places
(perps). The gate sizes with plain floats; this module makes those values valid
for the wire — size is rounded **down** (never past a risk cap), price to the
nearest valid tick (integer prices are always allowed).

Pure functions, no I/O: `szDecimals` comes from the keyless `/info meta` feed
(`MarksFeed.sz_decimals`), so paper and tests never need the SDK.
"""

from __future__ import annotations

from decimal import ROUND_DOWN, Decimal

_MAX_PX_DECIMALS = 6  # perps: price decimals ≤ 6 − szDecimals
_PX_SIGNIFICANT_FIGURES = 5


def round_size(size: float, sz_decimals: int) -> float:
    """Floor to the asset's size precision — rounding up could breach a cap."""
    quantum = Decimal(1).scaleb(-sz_decimals)
    return float(Decimal(str(size)).quantize(quantum, rounding=ROUND_DOWN))


def round_price(px: float, sz_decimals: int) -> float:
    """Nearest price the exchange accepts: 5 significant figures, then at most
    `6 − szDecimals` decimals (mirrors the SDK's own rounding rule)."""
    significant = float(f"{px:.{_PX_SIGNIFICANT_FIGURES}g}")
    return round(significant, max(0, _MAX_PX_DECIMALS - sz_decimals))
