"""The journal's LLM half (PLAN.md §15.3): one opus call reflecting on the day's
digest. Strictly out-of-path — the input is our own tallied outcomes (never raw
external text), the output is prose appended to the journal file, and nothing here
can reach config, the gate, or an order.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.llm import make_client

SYSTEM_PROMPT = (
    "You are a senior discretionary trader writing tonight's journal entry for your "
    "own desk. You are reviewing one day of a systematic book: an LLM decides which "
    "supplied setups to take inside a deterministic risk gate, and a rule-based "
    "manager (with gated LLM assistance) runs the open positions.\n\n"
    "From the day digest you are given, write a short reflection: what worked, what "
    "didn't, whether the skips/rejections look like discipline or missed opportunity, "
    "and 1-3 concrete lessons to carry forward. Judge process, not just P&L — a "
    "well-skipped bad setup is a win. Be specific to the data, not generic. "
    "Under 300 words, plain markdown paragraphs and bullets, no headings, no preamble."
)


def narrate(digest_markdown: str, caps: Caps, *, client=None) -> str | None:
    """The day's reflection, or None when the model returns nothing usable."""
    client = client or make_client()
    response = client.messages.create(
        model=caps.journal_model,
        max_tokens=caps.journal_max_tokens,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": digest_markdown}],
    )
    text = "\n".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
    return text or None
