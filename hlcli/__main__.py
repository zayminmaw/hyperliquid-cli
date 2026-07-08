"""`hl` entry point. Presents known domain errors as clean CLI messages."""

from __future__ import annotations

from hlcli.cli.errors import DOMAIN_ERRORS, render_domain_error


def main() -> None:
    from hlcli.cli.app import app  # deferred so import-time stays light

    try:
        app()
    except DOMAIN_ERRORS as exc:
        render_domain_error(exc)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
