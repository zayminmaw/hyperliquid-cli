"""The decision layer (PLAN.md §6).

Phase 2 is a **deterministic stub**: act now, full conviction — the gate is the
sole safety authority while the LLM is absent. Phase 3 replaces `decide` with the
`claude-sonnet-4-6` structured-output call; the gate and everything downstream
stay identical, which is the whole point of the judgment/mechanics split.
"""

from __future__ import annotations

from hlcli.core.types import Action, Candidate, Decision, Timing


def decide(candidate: Candidate) -> Decision:
    return Decision(
        candidate_id=candidate.id,
        action=Action.ACT,
        timing=Timing.NOW,
        conviction=1.0,
        rationale="deterministic stub (no LLM until Phase 3)",
    )
