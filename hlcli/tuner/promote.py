"""Proposal artifacts + promotion (PLAN.md §10).

`tune run` writes proposals here; they are **never** active until a human promotes
them. All artifacts live beside the active config:

    active_config.json  proposed_config.json   (the tunable surface)
    active_prompt.md     proposed_prompt.md     (the decision prompt)
    promotions.jsonl     (append-only audit trail)

`promote` re-clamps a config proposal before it becomes active — defence in depth,
even though `load_tunable` clamps again on every read — so a hand-edited proposal
can never widen the box.

Promotion *consumes* the proposal file: a proposal can go live exactly once, so a
stale one from weeks ago can't be silently re-promoted after newer `tune run`s
produced nothing. Each audit entry records what went live (the full config / the
prompt's hash+size), not just that something did.
"""

from __future__ import annotations

import difflib
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig, clamp


@dataclass
class TunerPaths:
    active_config: Path
    proposed_config: Path
    active_prompt: Path
    proposed_prompt: Path
    promotions: Path


def paths(caps: Caps) -> TunerPaths:
    base = caps.config_path
    return TunerPaths(
        active_config=base,
        proposed_config=base.with_name("proposed_config.json"),
        active_prompt=base.with_name("active_prompt.md"),
        proposed_prompt=base.with_name("proposed_prompt.md"),
        promotions=base.with_name("promotions.jsonl"),
    )


def pending_proposals(caps: Caps) -> list[str]:
    """Proposal files awaiting `tune promote` — surfaced by `agent status` and the journal."""
    p = paths(caps)
    return [f.name for f in (p.proposed_config, p.proposed_prompt) if f.exists()]


def write_proposed_config(caps: Caps, cfg: TunableConfig) -> Path:
    p = paths(caps).proposed_config
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(clamp(cfg).model_dump_json(indent=2))
    return p


def write_proposed_prompt(caps: Caps, text: str) -> Path:
    p = paths(caps).proposed_prompt
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def promote(caps: Caps, *, kinds: tuple[str, ...] = ("config", "prompt"), now: float | None = None) -> list[dict]:
    """Move existing proposals to active, appending each to the audit trail."""
    now = now if now is not None else time.time()
    p = paths(caps)
    promoted: list[dict] = []

    if "config" in kinds and p.proposed_config.exists():
        cfg = clamp(TunableConfig.model_validate_json(p.proposed_config.read_text()))
        p.active_config.parent.mkdir(parents=True, exist_ok=True)
        p.active_config.write_text(cfg.model_dump_json(indent=2))
        p.proposed_config.unlink()  # consumed — promotable exactly once
        promoted.append(_record(p.promotions, {"ts": now, "kind": "config", "config": cfg.model_dump()}))

    if "prompt" in kinds and p.proposed_prompt.exists():
        text = p.proposed_prompt.read_text()
        p.active_prompt.write_text(text)
        p.proposed_prompt.unlink()  # consumed
        promoted.append(_record(p.promotions, {
            "ts": now, "kind": "prompt",
            "sha256": hashlib.sha256(text.encode()).hexdigest(), "chars": len(text),
        }))

    return promoted


def history(caps: Caps) -> list[dict]:
    p = paths(caps).promotions
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def diff(caps: Caps) -> dict:
    """Proposed-vs-active diff for review: per-field for config, unified text for the prompt."""
    p = paths(caps)
    return {
        "config": _config_diff(p.active_config, p.proposed_config),
        "prompt": _prompt_diff(p.active_prompt, p.proposed_prompt),
    }


def _config_diff(active: Path, proposed: Path) -> dict | str:
    if not proposed.exists():
        return "no proposal"
    new = clamp(TunableConfig.model_validate_json(proposed.read_text())).model_dump()
    old = clamp(TunableConfig.model_validate_json(active.read_text())).model_dump() if active.exists() \
        else clamp(TunableConfig()).model_dump()
    return {k: {"active": old.get(k), "proposed": v} for k, v in new.items() if old.get(k) != v}


def _prompt_diff(active: Path, proposed: Path) -> str:
    if not proposed.exists():
        return "no proposal"
    old = active.read_text().splitlines() if active.exists() else []
    new = proposed.read_text().splitlines()
    return "\n".join(difflib.unified_diff(old, new, "active_prompt", "proposed_prompt", lineterm="")) or "identical"


def _record(promotions: Path, entry: dict) -> dict:
    with promotions.open("a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry
