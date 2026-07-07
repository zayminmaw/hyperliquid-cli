"""Watched intake directory — the producer-agnostic signal handoff (PLAN.md §15.1).

Any producer drops a JSON batch (a list of candidate dicts, or a single one) into
`<intake_dir>/<network>/`. Each poll: parse → enqueue → archive the file into
`processed/` (`failed/` + alert when unparseable). Enqueue happens *before* the
move, so a crash in between just re-parses next start — the content-hash candidate
ids make that a duplicate, not a double-queue.

Producers must write atomically (write to a temp name, then rename into the dir);
the settle window below is only a backstop against non-atomic writers.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from hlcli.core.config import Caps
from hlcli.core.types import Network
from hlcli.executor.intake import parse_batch
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore

_SETTLE_SECONDS = 2.0  # a file this fresh may still be mid-write; next poll gets it


@dataclass
class IntakeResult:
    files: int = 0
    enqueued: int = 0
    duplicates: int = 0
    failed: int = 0


def intake_dir(caps: Caps, network: Network) -> Path:
    return (caps.agent_intake_dir or caps.data_dir / "intake") / network.value


def poll(directory: Path, state: StateStore, alerter: Alerter, *, now: float | None = None) -> IntakeResult:
    now = time.time() if now is None else now
    result = IntakeResult()
    if not directory.exists():
        return result
    for path in sorted(directory.glob("*.json")):
        if now - path.stat().st_mtime < _SETTLE_SECONDS:
            continue
        result.files += 1
        try:
            data = json.loads(path.read_text())
            candidates = parse_batch(data if isinstance(data, list) else [data])
        except (json.JSONDecodeError, ValueError, KeyError, TypeError) as exc:
            # Bad content is quarantined loudly, never deleted — the raw batch is evidence.
            _archive(path, "failed")
            alerter.alert("intake_file_failed", level="warning", file=path.name, error=str(exc))
            result.failed += 1
            continue
        enqueued = sum(state.enqueue(c) for c in candidates)
        result.enqueued += enqueued
        result.duplicates += len(candidates) - enqueued
        _archive(path, "processed")
    return result


def _archive(path: Path, subdir: str) -> None:
    dest_dir = path.parent / subdir
    dest_dir.mkdir(exist_ok=True)
    dest = dest_dir / path.name
    if dest.exists():  # same filename dropped twice — keep both for the audit trail
        dest = dest_dir / f"{path.stem}-{int(time.time() * 1000)}{path.suffix}"
    path.rename(dest)
