"""Sentry 6a — deterministic in-trade rules (PLAN.md §14).

Pure evaluation: given an open ledger trade, the current mark, and a candle tail,
propose the actions practitioner trade-management dictates — a breakeven ratchet,
an ATR/percent trail, a one-shot scale-out. No I/O here; the apply layer owns
orders and persistence.

Invariants the rules guarantee (tests pin them):
  - the stop only ever moves toward profit (ratchet) — it never widens;
  - a proposed stop stays strictly on the losing side of the mark (a stop at or
    past the mark would fire on the next tick);
  - moves smaller than `min_move_r` of initial risk are suppressed (churn guard);
  - scale-out happens at most once per trade (`scaled_out` flag).

Everything is measured in R — the trade's *initial* risk `|entry − initial_sl|` —
so the rules stay coherent after the stop has already been ratcheted.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlcli.core.config_schema import TrailConfig
from hlcli.core.types import Candle, Side
from hlcli.executor.rmath import initial_risk

_ATR_PERIOD = 14


@dataclass(frozen=True)
class ScaleOut:
    """Close `size` contracts, booked at the ladder `level` (`r` R past entry)."""

    size: float
    level: float
    r: float


@dataclass(frozen=True)
class MoveStop:
    new_sl: float
    reason: str  # "breakeven" | "trail"


def active(cfg: TrailConfig) -> bool:
    """Whether any 6a rule is switched on — an all-off config costs a pass nothing."""
    return cfg.style != "off" or cfg.breakeven_trigger_r > 0 or cfg.scale_out_r > 0


def plan(trade: dict, mark: float, bars: list[Candle], cfg: TrailConfig) -> list[ScaleOut | MoveStop]:
    """Actions due for one open trade — scale-out first, so the stop that follows
    guards the remainder."""
    side = Side(trade["side"])
    entry = trade["entry"]
    risk = initial_risk(trade)
    if risk <= 0 or mark <= 0:
        return []
    favorable = (mark - entry) if side is Side.LONG else (entry - mark)
    r_now = favorable / risk

    actions: list[ScaleOut | MoveStop] = []
    scale = _scale_out(trade, side, entry, risk, r_now, cfg)
    if scale is not None:
        actions.append(scale)
    move = _move_stop(trade, side, entry, risk, r_now, mark, bars, cfg)
    if move is not None:
        actions.append(move)
    return actions


def _scale_out(trade: dict, side: Side, entry: float, risk: float, r_now: float,
               cfg: TrailConfig) -> ScaleOut | None:
    if cfg.scale_out_r <= 0 or trade["scaled_out"] or r_now < cfg.scale_out_r:
        return None
    offset = cfg.scale_out_r * risk
    level = entry + offset if side is Side.LONG else entry - offset
    return ScaleOut(size=trade["size"] * cfg.scale_out_fraction, level=level, r=cfg.scale_out_r)


def _move_stop(trade: dict, side: Side, entry: float, risk: float, r_now: float,
               mark: float, bars: list[Candle], cfg: TrailConfig) -> MoveStop | None:
    proposals: list[MoveStop] = []
    if cfg.breakeven_trigger_r > 0 and r_now >= cfg.breakeven_trigger_r:
        buffer = cfg.breakeven_buffer_r * risk
        level = entry + buffer if side is Side.LONG else entry - buffer
        proposals.append(MoveStop(level, "breakeven"))
    distance = _trail_distance(mark, bars, cfg)
    if distance is not None and r_now >= cfg.trail_start_r:
        level = mark - distance if side is Side.LONG else mark + distance
        proposals.append(MoveStop(level, "trail"))
    if not proposals:
        return None

    # The most profit-protecting proposal wins; then the ratchet + churn + mark
    # guards decide whether it is worth acting on at all.
    best = max(proposals, key=lambda p: p.new_sl if side is Side.LONG else -p.new_sl)
    improvement = (best.new_sl - trade["sl"]) if side is Side.LONG else (trade["sl"] - best.new_sl)
    if improvement <= 0 or improvement < cfg.min_move_r * risk:
        return None
    if (best.new_sl >= mark) if side is Side.LONG else (best.new_sl <= mark):
        return None
    return best


def _trail_distance(mark: float, bars: list[Candle], cfg: TrailConfig) -> float | None:
    if cfg.style == "percent":
        return mark * cfg.trail_percent / 100.0
    if cfg.style == "atr":
        value = atr(bars)
        return value * cfg.atr_multiple if value is not None else None
    return None


def atr(bars: list[Candle], period: int = _ATR_PERIOD) -> float | None:
    """Average true range of the last `period` bars; None when history is too short
    (an ATR trail then simply stays put — missing data must never widen a stop)."""
    if len(bars) < period + 1:
        return None
    window = bars[-(period + 1):]
    ranges = [
        max(cur.h - cur.l, abs(cur.h - prev.c), abs(cur.l - prev.c))
        for prev, cur in zip(window, window[1:])
    ]
    return sum(ranges) / period
