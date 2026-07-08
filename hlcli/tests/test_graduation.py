"""Graduation checklist (PLAN.md §7): resolved-trade track record vs hard-cap
thresholds → mainnet-readiness verdict."""

from hlcli.safety.graduation import assess
from hlcli.tests._helpers import caps

DAY = 86_400.0


def _trade(status="won", r=1.0, closed_at=0.0) -> dict:
    return {"status": status, "r_multiple": r, "realized": r * 10, "closed_at": closed_at}


def _record(n: int, *, r: float, span_days: float, won: bool = True) -> list[dict]:
    """n resolved trades evenly spread across `span_days`."""
    step = (span_days * DAY) / max(1, n - 1)
    return [_trade("won" if won else "lost", r, i * step) for i in range(n)]


def test_ready_when_all_thresholds_clear():
    g = assess(_record(10, r=0.5, span_days=10), caps(graduation_min_trades=5, graduation_min_days=7))
    assert g["ready"] and all(g["checks"].values())
    assert g["n"] == 10 and g["span_days"] == 10.0


def test_too_few_trades_blocks():
    g = assess(_record(4, r=1.0, span_days=30), caps(graduation_min_trades=5, graduation_min_days=7))
    assert not g["ready"] and not g["checks"]["min_trades"]
    assert g["checks"]["min_days"] and g["checks"]["positive_expectancy"]


def test_too_short_a_window_blocks():
    g = assess(_record(20, r=1.0, span_days=2), caps(graduation_min_trades=5, graduation_min_days=7))
    assert not g["ready"] and not g["checks"]["min_days"]


def test_negative_expectancy_blocks():
    g = assess(_record(20, r=-0.3, span_days=30, won=False),
               caps(graduation_min_trades=5, graduation_min_days=7))
    assert not g["ready"] and not g["checks"]["positive_expectancy"]
    assert g["avg_r"] == -0.3


def test_empty_ledger_is_not_ready():
    g = assess([], caps())
    assert not g["ready"] and g["n"] == 0 and g["span_days"] == 0.0


def test_scaled_partials_do_not_count_toward_graduation():
    # 4 real positions + 6 scale-out partials must NOT reach a 5-trade bar: partials
    # are exits of a position, not distinct decisions that could unlock mainnet.
    trades = _record(4, r=1.0, span_days=10)
    trades += [_trade("scaled", r=0.5, closed_at=i * DAY) for i in range(6)]
    g = assess(trades, caps(graduation_min_trades=5, graduation_min_days=7))
    assert g["n"] == 4 and not g["checks"]["min_trades"]
