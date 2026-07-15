"""Prompt/strategy tuner (PLAN.md §10) — out of the order path, propose → approve.

The "self-tune the decision-making" piece: it pairs each logged LLM decision with
the outcome of the trade it produced and asks `claude-sonnet-5` to refine the
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
    "evidence-backed caution or encouragement from the record (which setups, sides, conviction "
    "levels, or lines of reasoning in the rationales paid off and which didn't). Do not invent "
    "rules the data doesn't support, and do not let the prompt grow without bound — fold new "
    "lessons into existing guidance where they overlap. Return the COMPLETE revised prompt as "
    "plain markdown — no preamble, no commentary, no code fences, no surrounding quotes."
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

    text = _strip_fences(_text(response))
    if not text:
        return PromptProposal(None, "empty_output")
    return PromptProposal(text, "ok")


def _strip_fences(text: str) -> str:
    """Drop a Markdown code fence wrapping the whole output — `promote` writes the text
    verbatim into active_prompt.md, so a fenced proposal would ship its fences."""
    t = text.strip()
    if t.startswith("```") and t.endswith("```"):
        lines = t.splitlines()
        return "\n".join(lines[1:-1]).strip()
    return t


def _pairs(state: StateStore, trades: list[dict]) -> list[dict]:
    """Join logged decisions to their trade outcomes by candidate id. The rationale is
    the point: refining a *prompt* means learning which reasoning preceded wins vs
    losses, not just which coins or conviction levels did."""
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
            "rationale": str(decision.get("rationale") or "")[:240],
        })
    return out


def _prompt(current_prompt: str, pairs: list[dict]) -> str:
    # The current prompt goes in a tag, not JSON-escaped into one long string —
    # markdown structure survives, and the model revises what it can actually read.
    return (
        "Revise the decision prompt below using the decisions-vs-outcomes record that follows.\n"
        f"<current_prompt>\n{current_prompt}\n</current_prompt>\n"
        f"<decisions_vs_outcomes>\n{json.dumps(pairs, indent=2)}\n</decisions_vs_outcomes>"
    )


def _text(response) -> str:
    return "".join(
        block.text for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    )


def _loads(value):
    if not value:
        return None
    return json.loads(value) if isinstance(value, str) else value
