"""Resolved-trade cohorts (PLAN.md §10).

The tuner learns from *resolved* trades only. This groups them into cohorts —
coin × side × conviction-bucket — and computes per-cohort win-rate and expectancy
(mean R-multiple). Cohorts below `MIN_COHORT_SAMPLES` are dropped, so a tuner with
no eligible cohort gets an empty list and never calls the model. Pure arithmetic;
no LLM, no I/O.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from statistics import mean

MIN_COHORT_SAMPLES = 5  # below this a cohort is too thin to learn from

# A `scaled` row is a sentry partial close. Banked at the profit ladder it's a win
# (profit taken on purpose is not a miss) — but the 6c manager may also bank a
# partial *loss* to cut risk, so the sign of the realized P&L decides.
def _is_win(t: dict) -> bool:
    return t["status"] == "won" or (t["status"] == "scaled" and (t["realized"] or 0) > 0)


@dataclass
class Cohort:
    key: str  # "BTC/long/high"
    n: int
    wins: int
    win_rate: float
    avg_r: float  # mean R-multiple — the cohort's expectancy in units of risk
    total_realized: float


def conviction_bucket(conviction: float) -> str:
    if conviction < 0.4:
        return "low"
    if conviction < 0.7:
        return "mid"
    return "high"


def cohorts(trades: list[dict], *, min_samples: int = MIN_COHORT_SAMPLES) -> list[Cohort]:
    """Eligible cohorts only — groups with fewer than `min_samples` trades are dropped."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[f"{t['coin']}/{t['side']}/{conviction_bucket(t['conviction'])}"].append(t)

    out = []
    for key, ts in sorted(groups.items()):
        if len(ts) < min_samples:
            continue
        wins = sum(1 for t in ts if _is_win(t))
        out.append(Cohort(
            key=key, n=len(ts), wins=wins, win_rate=round(wins / len(ts), 3),
            avg_r=round(mean(t["r_multiple"] for t in ts), 4),
            total_realized=round(sum(t["realized"] for t in ts), 4),
        ))
    return out


def summary(trades: list[dict]) -> dict:
    """Portfolio-wide resolved-trade stats."""
    if not trades:
        return {"n": 0, "wins": 0, "win_rate": 0.0, "avg_r": 0.0, "total_realized": 0.0}
    wins = sum(1 for t in trades if _is_win(t))
    return {
        "n": len(trades),
        "wins": wins,
        "win_rate": round(wins / len(trades), 3),
        "avg_r": round(mean(t["r_multiple"] for t in trades), 4),
        "total_realized": round(sum(t["realized"] for t in trades), 4),
    }
