"""The reflection memory's read side (PLAN.md §15.4): the bounded "recent lessons"
block injected into the decision and management contexts. Two hard caps
(`HL_AGENT_REFLECT_INJECT_MAX`, `HL_AGENT_REFLECT_MAX_CHARS`) keep it from ever
bloating or dominating a prompt; the tunable switch turns it off entirely. The
lessons themselves are distilled only from our own logged outcomes — never from
raw external text.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig
from hlcli.state.store import StateStore


def recent_lessons(state: StateStore, caps: Caps, tunable: TunableConfig) -> list[dict]:
    """Newest-first `{date, lesson}` rows for the prompt, or [] when the inject is off."""
    if not tunable.agent.reflection_inject:
        return []
    return [
        {"date": r["date"], "lesson": r["lesson"][: caps.agent_reflect_max_chars]}
        for r in state.recent_reflections(caps.agent_reflect_inject_max)
    ]
