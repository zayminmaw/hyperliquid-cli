"""The deterministic half of the journal (PLAN.md §15.3): one UTC day of the state
store, tallied and rendered. Every number here reconciles with `exec report` /
`sentry log` — this is the audit view, built before (and independent of) the LLM
narrative.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.state.store import StateStore


class DayDigest(BaseModel):
    date: str  # YYYY-MM-DD (UTC)
    network: str

    # executor decisions for the day
    decisions: list[dict] = Field(default_factory=list)  # per-verdict: coin/action/conviction/rationale
    decided: int = 0
    fired: int = 0
    shadow_fired: int = 0
    deferred: int = 0
    dropped: int = 0
    rejected: int = 0
    reject_reasons: dict[str, int] = Field(default_factory=dict)

    # trades
    opened: list[dict] = Field(default_factory=list)
    resolved: list[dict] = Field(default_factory=list)
    realized: float = 0.0
    wins: int = 0
    losses: int = 0
    avg_r: float | None = None
    profit_factor: float | None = None

    # sentry + operational
    sentry_actions: dict[str, int] = Field(default_factory=dict)
    alert_events: dict[str, int] = Field(default_factory=dict)

    # snapshot at write time (matches `exec report`)
    equity: float = 0.0
    open_positions: int = 0
    unrealized_pnl: float = 0.0
    breaker: str = "clear"
    deferred_pending: int = 0
    pending_proposals: list[str] = Field(default_factory=list)


def day_bounds(date: str) -> tuple[float, float]:
    """[start, end) of a YYYY-MM-DD UTC day as unix seconds."""
    start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return start.timestamp(), (start + timedelta(days=1)).timestamp()


def utc_date(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def build_digest(
    exchange: Exchange,
    state: StateStore,
    network: Network,
    date: str,
    *,
    alerts_path: Path | None = None,
    pending_proposals: list[str] | None = None,
) -> DayDigest:
    t0, t1 = day_bounds(date)
    d = DayDigest(date=date, network=network.value)

    _tally_decisions(d, state.decisions_between(t0, t1))
    _tally_trades(d, state, t0, t1)
    d.sentry_actions = dict(Counter(r["action"] for r in state.sentry_between(t0, t1)))
    d.alert_events = _tally_alerts(alerts_path, t0, t1)

    positions = exchange.get_positions()
    d.equity = exchange.equity()
    d.open_positions = len(positions)
    d.unrealized_pnl = round(sum(p.unrealized_pnl for p in positions), 4)
    d.breaker = "tripped" if state.breaker_tripped() else "clear"
    d.deferred_pending = state.deferred_count()
    d.pending_proposals = pending_proposals or []
    return d


def _tally_decisions(d: DayDigest, rows: list[dict]) -> None:
    for row in rows:
        gate = _loads(row["gate"])
        context = _loads(row["context"])
        decision = _loads(row["decision"])
        if decision:
            d.decided += 1
            # The verdict's own words — the reflection's raw material (a skip
            # without its rationale is unauditable).
            d.decisions.append({
                "coin": context.get("coin", ""),
                "action": decision.get("action"),
                "conviction": decision.get("conviction"),
                "rationale": (decision.get("rationale") or "")[:240],
            })
        outcome = context.get("outcome") or _legacy_outcome(gate, context)
        if outcome == "dropped":
            d.dropped += 1
        elif outcome == "deferred":
            d.deferred += 1
        elif outcome == "rejected":
            d.rejected += 1
            reason = gate.get("reason") or context.get("rejected") or context.get("wait")
            d.reject_reasons[reason or "unknown"] = d.reject_reasons.get(reason or "unknown", 0) + 1


def _legacy_outcome(gate: dict, context: dict) -> str | None:
    """Classify a decision-log row written before the `outcome` field existed."""
    if context.get("dropped"):
        return "dropped"
    if context.get("wait") == "deferred":
        return "deferred"
    if gate.get("approved") is False or "rejected" in context or "wait" in context:
        return "rejected"
    return None


def _tally_trades(d: DayDigest, state: StateStore, t0: float, t1: float) -> None:
    for t in state.trades_opened_between(t0, t1):
        # A `scaled` row is a partial *exit* of a trade opened earlier, sharing its
        # `opened_at` — counting it as opened/fired would double-count the entry.
        if t["status"] == "scaled":
            continue
        d.opened.append({"coin": t["coin"], "side": t["side"], "size": t["size"],
                         "entry": t["entry"], "conviction": t["conviction"],
                         "shadow": bool(t["shadow"])})
        if t["shadow"]:
            d.shadow_fired += 1
        else:
            d.fired += 1

    day_resolved = [t for t in state.resolved_between(t0, t1) if not t["shadow"]]
    gross_win = gross_loss = 0.0
    r_values = []
    for t in day_resolved:
        realized = t["realized"] or 0.0
        d.resolved.append({"coin": t["coin"], "side": t["side"], "status": t["status"],
                           "realized": realized, "r_multiple": t["r_multiple"]})
        d.realized += realized
        if realized > 0:
            d.wins += 1
            gross_win += realized
        elif realized < 0:
            d.losses += 1
            gross_loss += -realized
        if t["r_multiple"] is not None:
            r_values.append(t["r_multiple"])
    d.realized = round(d.realized, 4)
    if r_values:
        d.avg_r = round(sum(r_values) / len(r_values), 3)
    if gross_loss > 0:
        d.profit_factor = round(gross_win / gross_loss, 3)


def _tally_alerts(path: Path | None, t0: float, t1: float) -> dict[str, int]:
    """Warning+ events from the JSONL alerts log — breaker trips, protection
    failures, halted passes. Unreadable lines are skipped: the log is append-only
    prose for ops, not a source the journal should crash on."""
    if path is None or not path.exists():
        return {}
    events: Counter[str] = Counter()
    for line in path.read_text().splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if t0 <= record.get("ts", 0) < t1 and record.get("level") in ("warning", "critical"):
            events[record.get("event", "unknown")] += 1
    return dict(events)


def render(d: DayDigest) -> str:
    """The journal's deterministic markdown. The narrative section is appended by
    the writer; everything above it must be reproducible from the state store."""
    lines = [
        f"# Trade journal — {d.network} — {d.date}",
        "",
        "## Day at a glance",
        "",
        f"- equity: **{d.equity}** · realized today: **{d.realized}** · unrealized: {d.unrealized_pnl}",
        f"- open positions: {d.open_positions} · deferred pending: {d.deferred_pending} · breaker: {d.breaker}",
        f"- pending tuner proposals: {', '.join(d.pending_proposals) or 'none'}",
        "",
        "## Executor",
        "",
        f"- decisions: {d.decided} · fired: {d.fired} (+{d.shadow_fired} shadow) · "
        f"deferred: {d.deferred} · rejected: {d.rejected} · dropped: {d.dropped}",
    ]
    if d.reject_reasons:
        lines.append("- rejections by reason:")
        lines += [f"  - {reason}: {n}" for reason, n in sorted(d.reject_reasons.items(), key=lambda kv: -kv[1])]
    if d.decisions:
        lines.append("- verdicts:")
        for v in d.decisions:
            rationale = f" — {v['rationale']}" if v["rationale"] else ""
            lines.append(f"  - {v['coin']}: {v['action']} (conviction {v['conviction']}){rationale}")

    lines += ["", "## Trades", ""]
    if d.opened:
        lines.append("| opened | side | size | entry | conviction | shadow |")
        lines.append("|---|---|---|---|---|---|")
        lines += [f"| {t['coin']} | {t['side']} | {t['size']} | {t['entry']} | {t['conviction']} "
                  f"| {'yes' if t['shadow'] else ''} |" for t in d.opened]
        lines.append("")
    if d.resolved:
        lines.append("| resolved | side | status | realized | R |")
        lines.append("|---|---|---|---|---|")
        lines += [f"| {t['coin']} | {t['side']} | {t['status']} | {t['realized']} "
                  f"| {t['r_multiple'] if t['r_multiple'] is not None else ''} |" for t in d.resolved]
        lines.append("")
        pf = d.profit_factor if d.profit_factor is not None else "n/a"
        avg = d.avg_r if d.avg_r is not None else "n/a"
        lines.append(f"- resolved: {len(d.resolved)} · wins: {d.wins} · losses: {d.losses} · "
                     f"avg R: {avg} · profit factor: {pf}")
    if not d.opened and not d.resolved:
        lines.append("no trades today")

    lines += ["", "## Sentry", ""]
    lines.append("- " + (" · ".join(f"{a}: {n}" for a, n in sorted(d.sentry_actions.items()))
                         if d.sentry_actions else "no management actions"))

    lines += ["", "## Operational alerts", ""]
    lines.append("- " + (" · ".join(f"{e}: {n}" for e, n in sorted(d.alert_events.items()))
                         if d.alert_events else "none (warning+)"))
    return "\n".join(lines) + "\n"


def _loads(value) -> dict:
    return json.loads(value) if value else {}
