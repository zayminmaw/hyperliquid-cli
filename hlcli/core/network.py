"""Network resolution + the mainnet gate (PLAN.md §3).

`paper` is the default everywhere. `mainnet` is *gated, not blocked*: it requires
ALL THREE of —
  1. the env flag `HL_ENABLE_MAINNET=1`  (a hard cap; see core.config),
  2. an explicit `--network mainnet`,
  3. a typed confirmation  (`-y` skips the prompt but NOT the env flag).

This module stays I/O-free and testable: the typed confirmation is supplied by the
caller as a `confirm` callable, so the CLI owns the prompt and the core owns the rule.
"""

from __future__ import annotations

from collections.abc import Callable

from hlcli.core.config import Caps
from hlcli.core.types import Network


class MainnetGateError(RuntimeError):
    """A mainnet action was requested without satisfying all three gate conditions."""


def resolve_network(requested: str | Network | None, caps: Caps) -> Network:
    """Pick the effective network: explicit request wins, else the configured default."""
    if requested is None:
        return caps.default_network
    return Network(requested)


def enforce_mainnet_gate(
    network: Network,
    caps: Caps,
    *,
    assume_yes: bool = False,
    confirm: Callable[[], bool] | None = None,
) -> None:
    """Raise `MainnetGateError` unless a mainnet action is fully authorized.

    No-op for paper/testnet. For mainnet:
      - the env flag must be set (condition 1; `--network mainnet` is condition 2,
        already implied by `network`),
      - then either `assume_yes` (automation) OR a truthy `confirm()` (condition 3).
    """
    if network is not Network.MAINNET:
        return

    if not caps.enable_mainnet:
        raise MainnetGateError(
            "mainnet is disabled. Set HL_ENABLE_MAINNET=1 to enable it (and re-check your caps)."
        )

    if assume_yes:
        return

    if confirm is None or not confirm():
        raise MainnetGateError("mainnet action not confirmed.")
