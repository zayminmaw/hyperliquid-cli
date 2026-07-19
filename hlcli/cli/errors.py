"""Domain-error rendering shared by the one-shot entry point and the REPL.

The `hl` entry point (`__main__`) and the interactive shell (`repl`) both map the
same set of known domain errors to a clean one-line message. Keeping the tuple and
the renderer here means the two surfaces stay identical without importing each other.
"""

from __future__ import annotations

from rich.console import Console

from hlcli._lazy import MissingDependencyError
from hlcli.accounts.keystore import KeystoreError
from hlcli.core.config_schema import ConfigError
from hlcli.core.network import MainnetGateError

# KeystoreError covers routine operator conditions ("key is encrypted — set
# HL_KEYSTORE_PASSPHRASE", wrong passphrase) that deserve a one-liner, not a traceback.
DOMAIN_ERRORS = (MainnetGateError, MissingDependencyError, ConfigError, KeystoreError)

_stderr = Console(stderr=True)


def render_error(message: str, console: Console | None = None) -> None:
    """Print a one-line CLI error (no traceback). Defaults to stderr for the `hl`
    entry point; the REPL passes its own console so all shell output stays one stream."""
    (console or _stderr).print(f"[red]error:[/red] {message}")


def render_domain_error(exc: BaseException, console: Console | None = None) -> None:
    """Print a known domain error as a clean CLI message (no traceback)."""
    render_error(str(exc), console)
