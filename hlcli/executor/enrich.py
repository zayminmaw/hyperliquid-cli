"""The enrich step (PLAN.md §5, §6).

Assembles the *decision context* the LLM judges a candidate against: the current
mark, portfolio state (equity + open positions + realized/unrealized P&L), a
rolling window of recent decisions, a short tail of recent price candles, the
code-computed market regime, and the tunable strategy surface. Pure assembly —
no exchange calls, no LLM, no keys; the runner gathers the per-pass inputs once
(candles + regime per coin) and feeds them here per candidate.

`regime` is computed deterministically from candles by `executor/regime.py` and
passed in; it is `None` when there isn't enough price history to judge, and the
gate treats `None` as "unknown, skip the regime check" — never a guess.
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
    regime: str | None = None  # code-computed (trend/range); None = unknown, gate skips the check
    candles: list[dict] | None = None  # compact recent OHLC tail; None when no price history
    recent_decisions: list[dict]  # what was recently decided/fired (no results yet)
    recent_outcomes: list[dict]  # resolved trades: what actually won/lost, in R
    followup: dict | None = None  # set on a WAIT re-check: attempts left + minutes to staleness
    tunable: dict


def enrich(
    candidate: Candidate,
    *,
    marks: dict[str, float],
    equity: float,
    positions: list[Position],
    realized: float | None,
    recent: list[dict],
    outcomes: list[dict] | None = None,
    tunable: TunableConfig,
    candles: list[dict] | None = None,
    regime: str | None = None,
    followup: dict | None = None,
) -> EnrichedContext:
    return EnrichedContext(
        candidate=candidate,
        mark=marks.get(candidate.coin),
        equity=round(equity, 4),
        realized_pnl=round(realized, 4) if realized is not None else None,
        unrealized_pnl=round(sum(p.unrealized_pnl for p in positions), 4),
        regime=regime,
        candles=candles,
        followup=followup,
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
        recent_decisions=_summarize_recent(recent),
        recent_outcomes=_summarize_outcomes(outcomes or []),
        # Only the *tunable* surface is exposed — never the hard caps or keys.
        tunable={
            "risk_per_trade_pct": tunable.risk_per_trade_pct,
            "allowed_regimes": list(tunable.regime.allowed_regimes),
            "min_conviction": tunable.sizing.min_conviction,
        },
    )


def _summarize_outcomes(trades: list[dict]) -> list[dict]:
    """Resolved trades as compact result rows — the model's actual track record,
    so "don't chase / don't force" has evidence behind it."""
    return [
        {
            "coin": t["coin"],
            "side": t["side"],
            "conviction": t["conviction"],
            "result": t["status"],
            "r": t["r_multiple"],
            "shadow": bool(t.get("shadow")),
        }
        for t in trades
    ]


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
