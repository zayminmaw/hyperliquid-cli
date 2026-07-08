"""The journal's LLM half (PLAN.md §15.3–.4): one opus call reflecting on the day's
digest. Strictly out-of-path — the input is our own tallied outcomes (never raw
external text), the output is prose appended to the journal file plus one distilled
lesson for the reflection memory. Nothing here can reach config, the gate, or an
order; the lesson enters future prompts only through the bounded inject.
"""

from __future__ import annotations

from dataclasses import dataclass

from hlcli.core.config import Caps
from hlcli.core.llm import make_client

SYSTEM_PROMPT = (
    "You are a senior discretionary trader writing tonight's journal entry for your "
    "own desk. You are reviewing one day of a systematic book: an LLM decides which "
    "supplied setups to take inside a deterministic risk gate, and a rule-based "
    "manager (with gated LLM assistance) runs the open positions.\n\n"
    "From the day digest you are given, submit tonight's entry: a short reflection "
    "(what worked, what didn't, whether the skips/rejections look like discipline or "
    "missed opportunity — judge process, not just P&L; a well-skipped bad setup is a "
    "win; under 300 words, plain markdown, no headings) and ONE lesson distilled for "
    "tomorrow's trade decisions. The lesson must be specific to today's data, "
    "actionable at decision time, and one sentence."
)

JOURNAL_TOOL = {
    "name": "submit_journal",
    "description": "Submit tonight's journal entry.",
    "input_schema": {
        "type": "object",
        "properties": {
            "reflection": {"type": "string", "description": "the journal reflection, plain markdown, under 300 words"},
            "lesson": {"type": "string", "description": "one distilled, decision-time-actionable lesson; a single sentence"},
        },
        "required": ["reflection", "lesson"],
    },
}


@dataclass
class JournalNarrative:
    reflection: str
    lesson: str | None  # None when the model returned no usable lesson


def narrate(digest_markdown: str, caps: Caps, *, client=None) -> JournalNarrative | None:
    """The day's reflection + distilled lesson, or None when the model returned
    nothing usable — dropped and surfaced by the writer, never guessed at."""
    client = client or make_client()
    response = client.messages.create(
        model=caps.journal_model,
        max_tokens=caps.journal_max_tokens,
        system=SYSTEM_PROMPT,
        tools=[JOURNAL_TOOL],
        tool_choice={"type": "tool", "name": "submit_journal"},
        messages=[{"role": "user", "content": digest_markdown}],
    )
    payload = _tool_payload(response)
    if payload is None:
        return None
    reflection = str(payload.get("reflection") or "").strip()
    if not reflection:
        return None
    lesson = str(payload.get("lesson") or "").strip()
    return JournalNarrative(reflection=reflection, lesson=lesson or None)


def _tool_payload(response) -> dict | None:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_journal":
            return block.input
    return None
