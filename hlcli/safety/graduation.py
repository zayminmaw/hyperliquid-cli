"""Graduation checklist (PLAN.md §7, §12 gate).

Before risking real money the executor should have earned it: enough resolved
trades, spread over enough days, with positive expectancy. This computes that
verdict from the resolved-trade ledger against hard-cap thresholds (risk policy —
*not* the LLM-tunable surface), so `exec report` can show whether the book is ready
for a controlled mainnet promotion. Pure arithmetic; reuses the tuner's `summary`.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.tuner.stats import summary


def assess(trades: list[dict], caps: Caps) -> dict:
    """Pass/fail readiness verdict plus the numbers behind each check.

    `scaled` rows are partial exits of a position, not distinct trading decisions —
    excluded here so `min_trades` counts positions, not banked partials (which would
    let a scale-out ladder inflate the track record and unlock mainnet early)."""
    graded = [t for t in trades if t.get("status") != "scaled"]
    stats = summary(graded)
    span_days = _span_days(graded)
    checks = {
        "min_trades": stats["n"] >= caps.graduation_min_trades,
        "min_days": span_days >= caps.graduation_min_days,
        "positive_expectancy": stats["avg_r"] > caps.graduation_min_expectancy,
    }
    return {
        "ready": all(checks.values()),
        "n": stats["n"],
        "win_rate": stats["win_rate"],
        "avg_r": stats["avg_r"],
        "span_days": span_days,
        "checks": checks,
        "thresholds": {
            "min_trades": caps.graduation_min_trades,
            "min_days": caps.graduation_min_days,
            "min_expectancy": caps.graduation_min_expectancy,
        },
    }


def _span_days(trades: list[dict]) -> float:
    """Days between the first and last resolved trade — the track-record window."""
    closed = [t["closed_at"] for t in trades if t.get("closed_at") is not None]
    if len(closed) < 2:
        return 0.0
    return round((max(closed) - min(closed)) / 86_400.0, 2)
