"""Lazy imports for optional, heavy, or key-requiring dependencies.

`paper` mode and the test suite must run without `anthropic`, the Hyperliquid SDK,
or signing libs installed. So those packages are NEVER imported at module top level
in hot paths — they are pulled in here, on first use, with a friendly error that
names the extra to install.
"""

from __future__ import annotations

import importlib
from types import ModuleType

# extra name -> the `pip install hyperliquid-cli[<extra>]` group that provides it
_EXTRA_FOR = {
    "anthropic": "llm",
    "hyperliquid": "exchange",
    "eth_account": "exchange",
    "httpx": "exchange",
    "websocket": "exchange",
}


def require(module: str) -> ModuleType:
    """Import `module`, or raise a clear install hint if it is missing."""
    try:
        return importlib.import_module(module)
    except ModuleNotFoundError as exc:
        extra = _EXTRA_FOR.get(module.split(".")[0])
        hint = f" Install it with: pip install 'hyperliquid-cli[{extra}]'" if extra else ""
        raise MissingDependencyError(f"'{module}' is not installed.{hint}") from exc


class MissingDependencyError(RuntimeError):
    """A lazily-imported optional dependency was requested but isn't installed."""
