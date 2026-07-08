"""Exponential backoff for the executor/sentry/agent retry loops — one formula so the
three loops can't drift apart (they once carried three hand-inlined copies)."""

from __future__ import annotations

_DOUBLING_CAP = 10  # stop doubling past 2**10 so the arithmetic can't overflow the cap


def backoff_delay(base: float, failures: int, max_delay: float) -> float:
    """`base` seconds while healthy; on a failure streak, `base` doubled per consecutive
    failure, capped at `max_delay`. `failures <= 0` is the steady-state interval."""
    if failures <= 0:
        return base
    return min(base * (2 ** min(failures, _DOUBLING_CAP)), max_delay)
