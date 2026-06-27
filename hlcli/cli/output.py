"""Output helpers — every command renders through here so `--json` is uniform.

Human mode → a compact rich table. `--json` mode → machine-readable JSON for
piping into `jq`, scripts, and cron. Commands should pass JSON-friendly payloads
(stringify Paths/enums upstream).
"""

from __future__ import annotations

from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

_console = Console()


def emit(payload: dict[str, Any], *, as_json: bool, title: str | None = None) -> None:
    """Render a flat key/value payload as a table, or as JSON when `--json` is set."""
    if as_json:
        _console.print_json(data=payload)
        return

    table = Table(title=title, show_header=False, box=box.SIMPLE)
    table.add_column("field", style="cyan", no_wrap=True)
    table.add_column("value")
    for key, value in payload.items():
        table.add_row(str(key), str(value))
    _console.print(table)


def note(message: str) -> None:
    """A one-line human status message (suppressed implicitly under `--json` callers)."""
    _console.print(message)
