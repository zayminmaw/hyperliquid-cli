"""The risk gate is the safety authority — test it hard: each reject, ordering, sizing."""

import time

import pytest

from hlcli.core.config import Caps
from hlcli.core.config_schema import ConvictionSizing, TunableConfig, clamp
from hlcli.core.types import Action, Candidate, Decision, Side, Timing
from hlcli.executor.gate import GateContext, evaluate, infer_side

NOW = 1_000_000.0


def _caps(**kw) -> Caps:
    base = dict(
        allowed_coins="BTC,ETH,SOL", starting_equity=10_000.0, max_notional_per_trade=1_000.0,
        max_concurrent_positions=3, max_leverage=3.0, rr_floor=1.5, max_signal_age_minutes=30,
    )
    return Caps(**{**base, **kw})


def _candidate(side=Side.LONG, entry=100.0, tp=120.0, sl=90.0, created_at=NOW, coin="BTC") -> Candidate:
    return Candidate(id="c1", coin=coin, side=side, entry=entry, tp=tp, sl=sl, created_at=created_at)


def _decision(action=Action.ACT, timing=Timing.NOW, conviction=1.0) -> Decision:
    return Decision(candidate_id="c1", action=action, timing=timing, conviction=conviction)


def _ctx(caps=None, **kw) -> GateContext:
    base = dict(
        caps=caps or _caps(), tunable=clamp(TunableConfig()), equity=10_000.0,
        open_coins=set(), open_count=0, now=NOW, mark=100.0,  # mark at the default entry
    )
    return GateContext(**{**base, **kw})


def _scaling_on() -> TunableConfig:
    """Conviction→size scaling is OFF by default (audit L-1); scaling tests opt in."""
    return clamp(TunableConfig(sizing=ConvictionSizing(enabled=True)))


# --- happy paths ---

def test_approves_coherent_long():
    out = evaluate(_candidate(), _decision(), _ctx())
    assert out.approved and out.order is not None and out.order.side is Side.LONG


def test_approves_coherent_short():
    c = _candidate(side=Side.SHORT, entry=100.0, tp=80.0, sl=110.0)
    out = evaluate(c, _decision(), _ctx())
    assert out.approved and out.order.side is Side.SHORT


# --- each rejection ---

@pytest.mark.parametrize("decision,reason", [
    (_decision(action=Action.SKIP), "skip"),
    (_decision(timing=Timing.WAIT), "wait"),
])
def test_rejects_non_actionable_decision(decision, reason):
    out = evaluate(_candidate(), decision, _ctx())
    assert not out.approved and reason in out.reason


def test_rejects_breaker():
    out = evaluate(_candidate(), _decision(), _ctx(breaker_tripped=True))
    assert not out.approved and "breaker" in out.reason


def test_rejects_daily_loss():
    out = evaluate(_candidate(), _decision(), _ctx(daily_loss_hit=True))
    assert not out.approved and "daily loss" in out.reason


def test_rejects_stale():
    out = evaluate(_candidate(created_at=NOW - 3600), _decision(), _ctx())
    assert not out.approved and "stale" in out.reason


def test_rejects_disallowed_coin():
    out = evaluate(_candidate(coin="DOGE"), _decision(), _ctx())
    assert not out.approved and "ALLOWED_COINS" in out.reason


def test_rejects_regime_when_enabled():
    out = evaluate(_candidate(), _decision(), _ctx(regime="chop"))
    assert not out.approved and "regime" in out.reason


def test_rejects_incoherent_levels():
    # long but tp below entry
    out = evaluate(_candidate(side=Side.LONG, entry=100, tp=95, sl=90), _decision(), _ctx())
    assert not out.approved and "incoherent" in out.reason


def test_rejects_low_rr():
    # risk 10, reward 5 -> rr 0.5 < 1.5
    out = evaluate(_candidate(entry=100, tp=105, sl=90), _decision(), _ctx())
    assert not out.approved and "R:R" in out.reason


# --- mark sanity: the entry is a MARKET order, so the mark is what we'd pay ---

def test_rejects_missing_mark():
    out = evaluate(_candidate(), _decision(), _ctx(mark=None))
    assert not out.approved and "no mark" in out.reason


@pytest.mark.parametrize("mark", [89.0, 90.0, 120.0, 125.0])  # beyond sl / at sl / at tp / beyond tp
def test_rejects_mark_outside_levels(mark):
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(mark=mark))
    assert not out.approved and "outside sl/tp" in out.reason


def test_rejects_entry_that_has_run():
    # mark 110: reward 10 / risk 20 = 0.5 R:R at mark, though the thesis R:R was 2.0
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(mark=110.0))
    assert not out.approved and "R:R at mark" in out.reason


def test_mark_retraced_toward_stop_still_approves():
    # mark 95: reward 25 / risk 5 = 5.0 R:R at mark — better fill than the thesis
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(mark=95.0))
    assert out.approved


def test_sizing_prices_risk_at_the_mark():
    # mark 95: stop distance 5 (not 10) → 50 risk / 5 = 10 units, notional at 95
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(conviction=1.0), _ctx(mark=95.0))
    assert out.size == 10.0 and out.notional == 950.0


def test_rejects_one_per_coin():
    out = evaluate(_candidate(), _decision(), _ctx(open_coins={"BTC"}))
    assert not out.approved and "already in a position" in out.reason


def test_rejects_max_concurrent():
    out = evaluate(_candidate(), _decision(), _ctx(open_count=3))
    assert not out.approved and "max concurrent" in out.reason


def test_rejects_zero_size_below_conviction():
    out = evaluate(_candidate(), _decision(conviction=0.1), _ctx(tunable=_scaling_on()))
    assert not out.approved and "size clamped to zero" in out.reason


def test_rejects_non_positive_equity():
    # A blown account rejects explicitly, not via a misleading "size clamped" reason.
    out = evaluate(_candidate(), _decision(), _ctx(equity=0.0))
    assert not out.approved and out.reason == "equity non-positive"


def test_approved_entry_is_marketable():
    # An accepted entry must be a filled one — a MARKET order, never a resting limit.
    from hlcli.core.types import OrderType
    out = evaluate(_candidate(), _decision(), _ctx())
    assert out.approved and out.order.order_type is OrderType.MARKET


# --- first-failure ordering ---

def test_breaker_beats_staleness():
    out = evaluate(_candidate(created_at=NOW - 3600), _decision(), _ctx(breaker_tripped=True))
    assert out.reason == "breaker tripped"


# --- sizing math ---

def test_fixed_fractional_sizing():
    # 0.5% * 10000 = 50 risk; stop 10 -> 5 units; conviction 1 -> ceil(1.0); under caps
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(conviction=1.0), _ctx())
    assert out.size == 5.0 and out.notional == 500.0


def test_notional_cap_clamps_size():
    caps = _caps(max_notional_per_trade=100.0)  # max 1 unit at price 100
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(caps=caps))
    assert out.size == 1.0 and out.notional == 100.0


def test_leverage_cap_clamps_size():
    caps = _caps(max_leverage=0.01)  # 10000*0.01/100 = 1 unit
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(caps=caps))
    assert out.size == 1.0


def test_conviction_scales_within_bounds():
    # at min_conviction (0.3) -> floor_fraction (0.25) of 5 units = 1.25
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(conviction=0.3),
                   _ctx(tunable=_scaling_on()))
    assert out.size == 1.25


def test_rejects_notional_below_exchange_minimum():
    # X-2: Hyperliquid rejects orders under $10 notional — the gate says so up front.
    # equity 100 → risk 0.5% = 0.5; stop 10 → size 0.05 → notional 5 < $10.
    out = evaluate(_candidate(entry=100, sl=90, tp=120), _decision(), _ctx(equity=100.0))
    assert not out.approved and "below exchange minimum" in out.reason


def test_flat_sizing_ignores_conviction_by_default():
    # Scaling OFF (the default, audit L-1): every conviction sizes at the full
    # fixed-fractional target — 0.5% of 10000 = 50 risk / stop 10 = 5 units — and a
    # low-conviction act is no longer zero-sized (conviction is a logged signal only).
    for conviction in (0.1, 0.3, 0.9, 1.0):
        out = evaluate(_candidate(entry=100, sl=90, tp=120),
                       _decision(conviction=conviction), _ctx())
        assert out.approved and out.size == 5.0, conviction


# --- side inference ---

def test_infer_side():
    assert infer_side(100, 120, 90) is Side.LONG
    assert infer_side(100, 80, 110) is Side.SHORT
    with pytest.raises(ValueError):
        infer_side(100, 95, 90)
