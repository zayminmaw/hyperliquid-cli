"""Runner liveness — is the supervisor detectably alive? (audit F; Vibe live/runtime/liveness.py)

The supervisor writes `LAST_TICK` every tick, so a *separate* process can tell whether it is
alive without any cooperation from it — which matters exactly when it can't cooperate: a
SIGKILL / host crash emits nothing, so the loop simply stops managing open positions in
silence, with only the resting native SL/TP still protecting them. The stale heartbeat is the
only signal, and it is read **fail-closed**: no heartbeat, or one older than the threshold, is
"not alive". This module only *classifies* — the caller decides whether to page or reconcile.
"""

from __future__ import annotations

from enum import Enum


class Liveness(str, Enum):
    NEVER = "never"  # no heartbeat ever written — the supervisor has not run on this book
    ALIVE = "alive"  # last tick within the staleness threshold
    STALE = "stale"  # a tick was recorded but is too old — the loop is stopped or wedged


def stale_after_seconds(poll_seconds: float, override: float = 0.0) -> float:
    """Age past which a heartbeat counts as dead. An explicit `override` (>0) wins; otherwise
    3× the intake poll interval — the same margin `agent status` has always allowed a slow tick."""
    return override if override > 0 else 3.0 * poll_seconds


def classify(last_tick_age: float | None, stale_after: float) -> Liveness:
    """Verdict from the heartbeat's age (seconds since the last tick; None = never written)."""
    if last_tick_age is None:
        return Liveness.NEVER
    return Liveness.ALIVE if last_tick_age <= stale_after else Liveness.STALE
