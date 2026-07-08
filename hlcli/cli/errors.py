"""Domain-error rendering shared by the one-shot entry point and the REPL.

The `hl` entry point (`__main__`) and the interactive shell (`repl`) both map the
same set of known domain errors to a clean one-line message. Keeping the tuple and
the renderer here means the two surfaces stay identical without importing each other.
"""

from __future__ import annotations

from rich.console import Console

from hlcli._lazy import MissingDependencyError
from hlcli.core.config_schema import ConfigError
from hlcli.core.network import MainnetGateError

DOMAIN_ERRORS = (MainnetGateError, MissingDependencyError, ConfigError, NotImplementedError)

_stderr = Console(stderr=True)


def render_domain_error(exc: BaseException) -> None:
    """Print a known domain error as a clean CLI message (no traceback)."""
    _stderr.print(f"[red]error:[/red] {exc}")
