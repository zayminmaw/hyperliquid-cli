"""The executor pass (PLAN.md §5).

Phase 0 is the skeleton: resolve positions vs marks, then no-op. The real stages —
intake → enrich → LLM decision → gate → execute → monitor — are filled in across
Phases 2–3. The gate to pass Phase 0 is simply that `exec once` on paper runs this
cleanly and reports a no-op.
"""

from __future__ import annotations

from pydantic import BaseModel

from hlcli.core.types import Network
from hlcli.exchange.base import Exchange


class PassSummary(BaseModel):
    network: Network
    open_positions: int
    candidates_seen: int
    fired: int
    skipped: int
    note: str


def run_once(exchange: Exchange, *, dry_run: bool = False) -> PassSummary:
    """Run one executor pass. Phase 0: a clean no-op over current positions."""
    positions = exchange.get_positions()
    return PassSummary(
        network=exchange.network,
        open_positions=len(positions),
        candidates_seen=0,
        fired=0,
        skipped=0,
        note="no-op: intake wired in Phase 2, LLM decision in Phase 3"
        + (" (dry-run)" if dry_run else ""),
    )
