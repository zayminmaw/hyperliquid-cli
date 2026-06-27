"""`hl` entry point. Presents known domain errors as clean CLI messages."""

from __future__ import annotations

from rich.console import Console

from hlcli._lazy import MissingDependencyError
from hlcli.core.config_schema import ConfigError
from hlcli.core.network import MainnetGateError


def main() -> None:
    from hlcli.cli.app import app  # deferred so import-time stays light

    try:
        app()
    except (MainnetGateError, MissingDependencyError, ConfigError, NotImplementedError) as exc:
        Console(stderr=True).print(f"[red]error:[/red] {exc}")
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
