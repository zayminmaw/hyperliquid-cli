"""Shared test helpers — fake marks + caps/tunable factories + deterministic deciders.

The deciders stand in for the LLM so executor-mechanics tests run with no key and
no `anthropic` install; the real `decide` call is exercised separately in
test_decision.py against a fake client.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig, clamp
from hlcli.core.types import Action, Decision, Timing
from hlcli.executor.decision import DecisionResult


def act_now(ctx, caps, tunable) -> DecisionResult:
    """Deterministic 'act now, full conviction' — the Phase-2 stub, as an injectable decider."""
    return DecisionResult(
        Decision(candidate_id=ctx.candidate.id, action=Action.ACT, timing=Timing.NOW, conviction=1.0),
        raw={"action": "act", "timing": "now", "conviction": 1.0},
        note="ok",
    )


def drop(ctx, caps, tunable) -> DecisionResult:
    """A decider whose output failed schema validation — drop + tally, never fire."""
    return DecisionResult(None, raw=None, note="schema_invalid")


def act_wait(minutes: float = 1.0):
    """Factory for a decider that says 'act, but WAIT' with a fixed recheck delay."""
    def _decide(ctx, caps, tunable) -> DecisionResult:
        return DecisionResult(
            Decision(candidate_id=ctx.candidate.id, action=Action.ACT, timing=Timing.WAIT,
                     conviction=0.8, recheck_in_minutes=minutes),
            raw={"action": "act", "timing": "wait", "conviction": 0.8, "recheck_in_minutes": minutes},
            note="ok",
        )
    return _decide


class FakeMarks:
    """Stand-in for MarksFeed with fixed prices — keeps paper fills network-free."""

    def __init__(self, marks: dict[str, float] | None = None) -> None:
        self._m = marks or {"BTC": 100.0, "ETH": 1500.0, "SOL": 50.0}

    def all_marks(self, *, force: bool = False) -> dict[str, float]:
        return self._m

    def mark(self, coin: str) -> float | None:
        return self._m.get(coin)

    def book(self, coin: str) -> dict:
        return {"coin": coin, "levels": [[], []]}

    def candles(self, coin: str, *, interval: str = "15m", lookback: int = 48) -> list:
        return []  # no synthetic history → regime stays None, gate behaviour unchanged


def caps(**kw) -> Caps:
    base = dict(
        allowed_coins="BTC,ETH,SOL", starting_equity=10_000.0, max_notional_per_trade=1_000.0,
        max_concurrent_positions=3, max_leverage=3.0, rr_floor=1.5, max_signal_age_minutes=30,
        daily_loss_limit_pct=5.0,
    )
    return Caps(**{**base, **kw})


def tunable() -> TunableConfig:
    return clamp(TunableConfig())
