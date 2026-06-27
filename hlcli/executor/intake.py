"""Candidate intake (PLAN.md §4, §5).

Builds validated `Candidate`s from `hl exec propose` (single) or a JSON/file batch
and enqueues them in the state stream. Side is inferred from level geometry;
incoherent levels are rejected here (fail fast) and again at the gate (defense in
depth). A stable `id` makes re-importing the same batch idempotent.
"""

from __future__ import annotations

import time
import uuid

from hlcli.core.types import Candidate
from hlcli.executor.gate import infer_side

# Accept friendly aliases from hand-written batches.
_ALIASES = {"pair": "coin", "reason": "reasoning"}


def make_candidate(
    coin: str,
    entry: float,
    tp: float,
    sl: float,
    *,
    reasoning: str = "",
    news: str = "",
    id: str | None = None,
    created_at: float | None = None,
) -> Candidate:
    side = infer_side(entry, tp, sl)  # raises ValueError on incoherent levels
    return Candidate(
        id=id or uuid.uuid4().hex,
        coin=coin.upper(),
        side=side,
        entry=entry,
        tp=tp,
        sl=sl,
        reasoning=reasoning,
        news=news,
        created_at=created_at if created_at is not None else time.time(),
    )


def candidate_from_dict(item: dict) -> Candidate:
    normalized = {_ALIASES.get(k, k): v for k, v in item.items()}
    return make_candidate(
        coin=normalized["coin"],
        entry=float(normalized["entry"]),
        tp=float(normalized["tp"]),
        sl=float(normalized["sl"]),
        reasoning=normalized.get("reasoning", ""),
        news=normalized.get("news", ""),
        id=normalized.get("id"),
        created_at=normalized.get("created_at"),
    )


def parse_batch(items: list[dict]) -> list[Candidate]:
    return [candidate_from_dict(item) for item in items]
