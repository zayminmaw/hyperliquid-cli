"""The enrich step (PLAN.md §5, §6).

Assembles the *decision context* the LLM judges a candidate against: the current
mark, portfolio state (equity + open positions + realized/unrealized P&L), a
rolling window of recent decisions, and the tunable strategy surface. Pure
assembly — no exchange calls, no LLM, no keys; the runner gathers the per-pass
inputs once and feeds them here per candidate.

`regime` is carried but left `None`: a real trend/range signal needs a
price-history feed we don't have yet, and the gate already treats `None` as
"unknown, skip the regime check". Fabricating a regime from a single mark
snapshot would be guessing — so we don't.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from hlcli.core.config_schema import TunableConfig
from hlcli.core.types import Candidate, Position


class EnrichedContext(BaseModel):
    """The full input the decision layer reasons over — JSON-serializable for the prompt + log."""

    candidate: Candidate
    mark: float | None
    equity: float
    realized_pnl: float | None = None
    unrealized_pnl: float
    open_positions: list[dict]
    regime: str | None = None  # None = unknown (no price-history feed yet); gate skips the check
    recent_outcomes: list[dict]
    tunable: dict


def enrich(
    candidate: Candidate,
    *,
    marks: dict[str, float],
    equity: float,
    positions: list[Position],
    realized: float | None,
    recent: list[dict],
    tunable: TunableConfig,
) -> EnrichedContext:
    return EnrichedContext(
        candidate=candidate,
        mark=marks.get(candidate.coin),
        equity=round(equity, 4),
        realized_pnl=round(realized, 4) if realized is not None else None,
        unrealized_pnl=round(sum(p.unrealized_pnl for p in positions), 4),
        open_positions=[
            {
                "coin": p.coin,
                "side": p.side.value,
                "size": p.size,
                "entry": p.entry_price,
                "uPnL": round(p.unrealized_pnl, 4),
            }
            for p in positions
        ],
        recent_outcomes=_summarize_recent(recent),
        # Only the *tunable* surface is exposed — never the hard caps or keys.
        tunable={
            "risk_per_trade_pct": tunable.risk_per_trade_pct,
            "allowed_regimes": list(tunable.regime.allowed_regimes),
            "min_conviction": tunable.sizing.min_conviction,
        },
    )


def _summarize_recent(recent: list[dict]) -> list[dict]:
    """Compress decision-log rows into a compact what-worked/what-didn't window."""
    out = []
    for row in recent:
        decision = _loads(row.get("decision"))
        fill = _loads(row.get("fill"))
        out.append(
            {
                "candidate": (decision or {}).get("candidate_id", row.get("candidate_id")),
                "action": (decision or {}).get("action"),
                "conviction": (decision or {}).get("conviction"),
                "fired": bool((fill or {}).get("accepted")) if fill else False,
            }
        )
    return out


def _loads(value):
    if not value:
        return None
    return json.loads(value) if isinstance(value, str) else value
