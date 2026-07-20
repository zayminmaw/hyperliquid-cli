"""Exponential backoff for the executor/sentry/agent retry loops — one formula so the
three loops can't drift apart (they once carried three hand-inlined copies)."""

from __future__ import annotations

_DOUBLING_CAP = 10  # stop doubling past 2**10 so the arithmetic can't overflow the cap

# Shared retry knobs for the bounded read/write retry loops (marks feed, reduce-only writes)
# so the common values live in one place. The *max* delay is intentionally NOT shared: the
# reduce-only path keeps its own tighter cap (a live position is unprotected while it retries),
# while the read path can afford a slightly wider one.
RETRY_ATTEMPTS = 3     # total tries before giving up
RETRY_BASE_DELAY = 0.5  # steady-state / first-backoff seconds


def backoff_delay(base: float, failures: int, max_delay: float) -> float:
    """`base` seconds while healthy; on a failure streak, `base` doubled per consecutive
    failure, capped at `max_delay`. `failures <= 0` is the steady-state interval."""
    if failures <= 0:
        return base
    return min(base * (2 ** min(failures, _DOUBLING_CAP)), max_delay)
