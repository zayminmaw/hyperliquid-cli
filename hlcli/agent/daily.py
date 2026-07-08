"""The agent's daily job (PLAN.md §15.3–.5): journal the day that just ended
(which also distills the reflection-memory lesson), run both tuners, auto-promote
on paper ONLY, and emit the daily report alert. Testnet and mainnet proposals wait
for a human `tune promote` — the LLM never promotes its own tunable surface on a
live network.
"""

from __future__ import annotations

import time

from hlcli.core.config import Caps
from hlcli.core.config_schema import load_tunable
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.executor.decision import load_decision_prompt
from hlcli.journal.digest import utc_date
from hlcli.journal.narrative import narrate
from hlcli.journal.writer import write_journal
from hlcli.safety.alerts import Alerter
from hlcli.safety.breaker import Breaker
from hlcli.safety.graduation import assess
from hlcli.state.store import StateStore
from hlcli.tuner.config_tuner import propose_config
from hlcli.tuner.promote import (
    pending_proposals,
    promote as promote_proposals,
    write_proposed_config,
    write_proposed_prompt,
)
from hlcli.tuner.prompt_tuner import propose_prompt


def run_daily(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    network: Network,
    alerter: Alerter,
    *,
    client=None,  # injected in tests; None ⇒ each LLM step builds the real one lazily
    now: float | None = None,
    tunable=None,  # injected in tests; None ⇒ the active tunable surface
) -> dict:
    now = time.time() if now is None else now
    tunable = tunable if tunable is not None else load_tunable()

    yesterday = utc_date(now - 86_400)
    narrate_fn = (lambda md, c: narrate(md, c, client=client)) if client is not None else narrate
    journal = write_journal(
        exchange, state, caps, network, yesterday,
        narrative=tunable.agent.journal_narrative,
        narrate_fn=narrate_fn, alerter=alerter,
        pending_proposals=pending_proposals(caps),
    )

    tuner = _run_tuners(state, caps, network, tunable, client, alerter)

    positions = exchange.get_positions()
    report = {
        "journal": str(journal),
        "tuner": tuner,
        "equity": exchange.equity(),
        "open_positions": len(positions),
        "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4),
        "breaker": "tripped" if Breaker(state, caps).tripped() else "clear",
        "deferred": state.deferred_count(),
        "graduation": assess(state.resolved_trades(), caps),
        "pending_proposals": pending_proposals(caps),
    }
    alerter.alert("agent_daily_report", **report)
    return report


def _run_tuners(state, caps, network, tunable, client, alerter: Alerter) -> dict:
    """Propose from the day's outcomes, then paper-only auto-promote. Isolated so a
    tuner fault (bad cohort, API hiccup after the sample gate) degrades this stage
    without failing the daily job — otherwise the whole job would re-run every backoff
    cycle, re-paying for the journal and re-calling the tuners all day."""
    try:
        # Both tuners are sample-gated — a thin record means no model call at all.
        cfg = propose_config(state, caps, tunable, client=client)
        prompt = propose_prompt(state, caps, load_decision_prompt(caps), client=client)
        written = []
        if cfg.proposed is not None:
            write_proposed_config(caps, cfg.proposed)
            written.append("config")
        if prompt.proposed is not None:
            write_proposed_prompt(caps, prompt.proposed)
            written.append("prompt")

        promoted = []
        if network is Network.PAPER and pending_proposals(caps):
            promoted = [p["kind"] for p in promote_proposals(caps)]
        return {"config": cfg.note, "prompt": prompt.note,
                "written": written, "promoted": promoted}
    except Exception as exc:
        alerter.alert("agent_tuner_failed", level="warning", error=str(exc))
        return {"error": str(exc)}
