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
from statistics import mean, stdev

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


# Statuses that carry a genuine setup outcome. `scaled` children duplicate the parent's
# conviction; `aborted`/`abort_failed` are mechanical protection failures, not verdicts
# on the setup — all three would pollute a calibration curve.
_CALIBRATION_STATUSES = ("won", "lost", "expired", "closed")


def conviction_calibration(trades: list[dict]) -> list[dict]:
    """Conviction-bucket → realized outcome, over every resolved trade with a real result.

    This is the evidence gate for re-enabling conviction→size scaling
    (`sizing.enabled`, off by default per the 2026-07 audit): scaling earns its way
    back only when higher buckets show higher avg_r on an adequate sample. No
    minimum-sample filter — thin buckets are the honest picture, shown with their n.
    Adopted rows are excluded: no LLM verdict sits behind them, and their conviction
    is 0.0 by construction — they would fill the low bucket with non-evidence."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        if t["status"] in _CALIBRATION_STATUSES and not t.get("adopted"):
            groups[conviction_bucket(t["conviction"])].append(t)

    out = []
    for bucket in ("low", "mid", "high"):
        ts = groups.get(bucket)
        if not ts:
            continue
        wins = sum(1 for t in ts if _is_win(t))
        # avg_r only over rows that actually carry an R — a missing R must not read
        # as a flat outcome in the table that gates re-enabling sizing.
        rs = [t["r_multiple"] for t in ts if t["r_multiple"] is not None]
        out.append({
            "bucket": bucket, "n": len(ts), "win_rate": round(wins / len(ts), 3),
            "avg_r": round(mean(rs), 4) if rs else None,
        })
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


_EXIT_ACTIONS = ("close", "reduce")


def sentry_exit_attribution(proposals: list[dict], final_r: dict[int, float]) -> dict:
    """Score the sentry-shadow log on realized R, not just agreement (audit J).

    Each shadow proposal is paired with the 6a baseline and records the trade's R at that
    instant (`r_now`). For a *diverging early exit* — the LLM said close/reduce where the rules
    would have held — the counterfactual is computable from realized R alone: banking `r_now`
    now versus letting the trade run to its final `r_multiple`. `delta_r = r_now − final_r`;
    a positive mean means the LLM's exits would have added R over the rules, a negative mean
    means it would have cut winners short. This is Vibe-Trading's delta-PnL idea in R units —
    the promotable signal the agreement tally can't give.

    Only close/reduce divergences are attributed: they're the proposals whose outcome follows
    from realized R without re-simulating the price path (a tighten/extend changes the path).
    `proposals` are the parsed shadow details (each carrying `trade_id`, `agrees`, `r_now`, and
    `proposal.action`); `final_r` maps trade id → resolved r_multiple."""
    deltas = []
    for p in proposals:
        if p.get("agrees") or p.get("proposal", {}).get("action") not in _EXIT_ACTIONS:
            continue
        r_now, fr = p.get("r_now"), final_r.get(p.get("trade_id"))
        if r_now is None or fr is None:
            continue
        deltas.append(r_now - fr)
    if not deltas:
        return {"exit_divergences": 0, "avg_delta_r": None, "total_delta_r": 0.0}
    return {"exit_divergences": len(deltas), "avg_delta_r": round(mean(deltas), 4),
            "total_delta_r": round(sum(deltas), 4)}


def management_cohorts(resolved: list[dict]) -> list[dict]:
    """Realized R grouped by which trade-management events fired — the deterministic evidence a
    sentry tuner acts on (audit J). `stop_moved` = the ratchet/trail moved the stop off its
    initial level; `scaled` = the one-shot scale-out banked a partial. Excludes mechanical
    failures (aborted/abort_failed) and rows without an R, the same hygiene the calibration
    table uses — a management verdict, not a protection glitch, is what we're grading."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in resolved:
        if t["status"] in ("aborted", "abort_failed") or t.get("r_multiple") is None:
            continue
        moved = t.get("initial_sl") is not None and t["sl"] != t["initial_sl"]
        scaled = bool(t.get("scaled_out")) or t["status"] == "scaled"
        key = ("stop_moved" if moved else "stop_initial") + "/" + ("scaled" if scaled else "full")
        groups[key].append(t)

    out = []
    for key in sorted(groups):
        ts = groups[key]
        wins = sum(1 for t in ts if _is_win(t))
        out.append({"cohort": key, "n": len(ts), "win_rate": round(wins / len(ts), 3),
                    "avg_r": round(mean(t["r_multiple"] for t in ts), 4)})
    return out


def _ratio(numerator: float, series: list[float]) -> float | None:
    """`numerator ÷ dispersion(series)`, or None on a sample too small / degenerate to trust.
    Sample stdev (ddof=1) needs ≥2 points, and a zero-dispersion series has no risk to divide
    by — either way the ratio is meaningless, so report None rather than a fabricated number."""
    if len(series) < 2:
        return None
    sd = stdev(series)
    return round(numerator / sd, 4) if sd > 0 else None


def _downside_deviation(returns: list[float]) -> float:
    """Root-mean-square of the negative returns (zeros for the rest) — the Sortino denominator."""
    downside = [min(0.0, r) for r in returns]
    return (sum(d * d for d in downside) / len(returns)) ** 0.5


def _avg_entry_slip_pct(trades: list[dict]) -> float | None:
    """Mean *adverse* entry slippage vs the mark at fire, over real fills that recorded one
    (audit D). Positive = filled worse than the mark: paid up on a long, sold cheap on a short.
    Shadow rows enter at the mark by construction (slip 0), so they're excluded."""
    slips = []
    for t in trades:
        mark = t.get("mark_at_entry")
        if t.get("shadow") or not mark:
            continue
        adverse = (t["entry"] - mark) if t["side"] == "long" else (mark - t["entry"])
        slips.append(adverse / mark * 100.0)
    return round(mean(slips), 4) if slips else None


def performance(trades: list[dict], *, starting_equity: float) -> dict:
    """Execution-quality metrics resolved trades hide behind win-rate/expectancy (audit C).

    Trade-based, not annualised: the return series is each closed trade's realized P&L over
    the running equity (a fixed base — this tool has no deposits/withdrawals), ordered by
    close time. Sharpe/Sortino are None below two trades or on a zero-dispersion series;
    `profit_factor` is None with no losing trades; `avg_slip_pct` is None with no measured
    fills. Drawdown is peak-to-trough on the reconstructed equity curve, in percent.

    Computed over whatever resolved set the caller passes — for `exec report` that is the
    whole book (real + shadow), same as graduation/calibration; on a DB that has run both
    real and shadow passes the equity curve therefore blends the two."""
    resolved = [t for t in trades if t.get("realized") is not None and t.get("closed_at") is not None]
    if not resolved:
        return {"n": 0, "profit_factor": None, "max_drawdown_pct": 0.0,
                "sharpe": None, "sortino": None, "avg_slip_pct": None, "total_fees": 0.0}
    ordered = sorted(resolved, key=lambda t: t["closed_at"])

    gains = sum(t["realized"] for t in ordered if t["realized"] > 0)
    losses = -sum(t["realized"] for t in ordered if t["realized"] < 0)
    profit_factor = round(gains / losses, 4) if losses > 0 else None

    equity = peak = starting_equity
    max_dd = 0.0
    returns: list[float] = []
    for t in ordered:
        if equity > 0:
            returns.append(t["realized"] / equity)
        equity += t["realized"]
        peak = max(peak, equity)
        if peak > 0:
            max_dd = max(max_dd, (peak - equity) / peak)

    mu = mean(returns) if returns else 0.0
    sortino = None
    if len(returns) >= 2:
        dd = _downside_deviation(returns)
        sortino = round(mu / dd, 4) if dd > 0 else None
    return {
        "n": len(ordered),
        "profit_factor": profit_factor,
        "max_drawdown_pct": round(max_dd * 100.0, 3),
        "sharpe": _ratio(mu, returns),
        "sortino": sortino,
        "avg_slip_pct": _avg_entry_slip_pct(ordered),
        # Round-trip taker fees already subtracted from the realized/PF/expectancy above
        # (wave-2 K) — surfaced so the operator can see the cost drag on the net numbers.
        "total_fees": round(sum(t.get("fee_paid") or 0.0 for t in ordered), 4),
    }
