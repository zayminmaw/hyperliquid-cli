"""Operational alerting (PLAN.md §7).

Fires, rejects, breaker trips, and loss-limit hits are emitted as structured JSON
lines — appended to `<data_dir>/alerts.log` and echoed to stderr. No external
service, no keys, no extra deps: a delivery channel (webhook, email) can tail the
log later. Injectable so the executor's hot path stays testable — tests pass an
`Alerter` with a captured stream (or none at all) instead of writing to disk.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import TextIO

from hlcli.core.config import Caps
from hlcli.core.types import Network


def alerts_path(caps: Caps, network: Network) -> Path:
    """The network's JSONL alert log. One definition so the writer that reads it and
    the alerter that writes it can never drift onto different filenames."""
    return caps.data_dir / f"alerts-{network.value}.log"


def network_alerter(caps: Caps, network: Network) -> Alerter:
    """Network-scoped alert sink: JSONL log beside the data dir + stderr."""
    return Alerter(alerts_path(caps, network))


class Alerter:
    """Sink for operational alerts. A `None` path skips the file; a `None` stream skips stderr."""

    def __init__(self, path: Path | None = None, *, stream: TextIO | None = sys.stderr) -> None:
        self._path = path
        self._stream = stream

    def alert(self, event: str, *, level: str = "info", **fields) -> dict:
        record = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
        line = json.dumps(record, default=str)
        if self._stream is not None:
            print(f"[alert:{level}] {line}", file=self._stream)
        if self._path is not None:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a") as fh:
                fh.write(line + "\n")
        return record
