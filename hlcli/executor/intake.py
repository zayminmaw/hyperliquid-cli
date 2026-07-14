"""Candidate intake (PLAN.md §4, §5).

Builds validated `Candidate`s from `hl exec propose` (single) or a JSON/file batch
and enqueues them in the state stream. Side is inferred from level geometry;
incoherent levels are rejected here (fail fast) and again at the gate (defense in
depth).

Idempotent re-import: a batch item without an explicit `id` gets one derived from
its *content*, so importing the same file twice enqueues nothing new. A CLI
`propose` (no file) still gets a random id — deliberately re-proposing the same
levels tomorrow is a new thesis, not a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import uuid

from hlcli.core.types import Candidate
from hlcli.executor.gate import infer_side

# Accept friendly aliases from hand-written batches.
_ALIASES = {"pair": "coin", "reason": "reasoning"}

# Imperative-injection heuristics for the human-supplied thesis text (2026-07 audit,
# L-5/E2/E3). The decision model *evaluates* `reasoning`/`news`, so text that commands
# it — rather than argues a setup — is the prompt-injection surface. Flagging is
# advisory: the flagged candidate still flows to the decision + gate (the gate remains
# the authority); the flags ride into the decision log and a warning alert so a
# poisoned intake feed is visible, not silent.
_INJECTION_PATTERNS = (
    ("ignore-instructions", re.compile(
        r"\b(ignore|disregard|forget)\b.{0,40}\b(instruction|rule|prompt|guideline|boundar)", re.I | re.S)),
    ("role-override", re.compile(r"\b(you are now|act as|new persona|system prompt|jailbreak)\b", re.I)),
    ("verdict-coercion", re.compile(
        r"\b(you must|always|are required to)\b.{0,30}\b(act|buy|sell|approve|execute|answer)\b", re.I | re.S)),
    ("schema-tamper", re.compile(r"\b(conviction|action|timing)\s*[:=]\s*\S", re.I)),
)


def injection_flags(candidate: Candidate) -> list[str]:
    """Names of injection heuristics the candidate's thesis text trips (often empty)."""
    text = f"{candidate.reasoning}\n{candidate.news}"
    return [name for name, rx in _INJECTION_PATTERNS if rx.search(text)]


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
        id=normalized.get("id") or _content_id(normalized),
        created_at=normalized.get("created_at"),
    )


def _content_id(normalized: dict) -> str:
    """A stable id from the item's content (created_at excluded when auto-assigned),
    so re-importing the same batch file dedupes instead of double-queueing."""
    material = json.dumps(
        {k: normalized.get(k) for k in ("coin", "entry", "tp", "sl", "reasoning", "news", "created_at")},
        sort_keys=True, default=str,
    )
    return hashlib.sha256(material.encode()).hexdigest()[:32]


def parse_batch(items: list[dict]) -> list[Candidate]:
    return [candidate_from_dict(item) for item in items]
