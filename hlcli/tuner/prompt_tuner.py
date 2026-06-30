"""Prompt/strategy tuner (PLAN.md §10) — out of the order path, propose → approve.

The "self-tune the decision-making" piece: it pairs each logged LLM decision with
the outcome of the trade it produced and asks `claude-opus-4-8` to refine the
*decision prompt* (e.g. "high-conviction shorts on SOL lost 4/5 — add caution").
Sample-gated on resolved-trade count; output is plain prompt text a human reviews
and promotes. It never edits sizing or caps — only the words the decision model reads.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from hlcli.core.config import Caps
from hlcli.core.llm import make_client
from hlcli.state.store import StateStore
from hlcli.tuner.stats import MIN_COHORT_SAMPLES

SYSTEM_PROMPT = (
    "You refine the decision prompt of a crypto-futures execution model from its own "
    "decisions-vs-outcomes record. You are out of the order path: a human reviews your draft "
    "before it goes live, and deterministic code still owns all sizing and safety.\n\n"
    "Keep the prompt's structure and its hard boundaries (the model judges act/skip, now/wait, "
    "conviction only — it never sizes, places stops, or overrides limits). Fold in concrete, "
    "evidence-backed caution or encouragement from the record (which setups, sides, or "
    "conviction levels paid off and which didn't). Do not invent rules the data doesn't support. "
    "Return the COMPLETE revised prompt as plain markdown — no preamble, no commentary."
)


@dataclass
class PromptProposal:
    proposed: str | None  # None = nothing proposed
    note: str  # "ok" | "insufficient_data" | "empty_output"


def propose_prompt(
    state: StateStore,
    caps: Caps,
    current_prompt: str,
    *,
    client=None,
    min_samples: int = MIN_COHORT_SAMPLES,
) -> PromptProposal:
    trades = state.resolved_trades()
    if len(trades) < min_samples:
        return PromptProposal(None, "insufficient_data")  # model is not called

    client = client or make_client()
    response = client.messages.create(
        model=caps.tuner_model,
        max_tokens=caps.tuner_max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _prompt(current_prompt, _pairs(state, trades))}],
    )

    text = _text(response).strip()
    if not text:
        return PromptProposal(None, "empty_output")
    return PromptProposal(text, "ok")


def _pairs(state: StateStore, trades: list[dict]) -> list[dict]:
    """Join logged decisions to their trade outcomes by candidate id."""
    outcomes = {t["candidate_id"]: t for t in trades}
    out = []
    for row in state.recent_decisions(limit=200):
        decision = _loads(row.get("decision"))
        if not decision:
            continue
        t = outcomes.get(decision.get("candidate_id"))
        if t is None:
            continue
        out.append({
            "coin": t["coin"], "side": t["side"], "conviction": decision.get("conviction"),
            "timing": decision.get("timing"), "result": t["status"], "r_multiple": t["r_multiple"],
        })
    return out


def _prompt(current_prompt: str, pairs: list[dict]) -> str:
    return json.dumps({"current_prompt": current_prompt, "decisions_vs_outcomes": pairs}, indent=2)


def _text(response) -> str:
    return "".join(
        block.text for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    )


def _loads(value):
    if not value:
        return None
    return json.loads(value) if isinstance(value, str) else value
