"""Network resolution + the three-condition mainnet gate (PLAN.md §3)."""

import pytest

from hlcli.core.config import Caps
from hlcli.core.network import MainnetGateError, enforce_mainnet_gate, resolve_network
from hlcli.core.types import Network


def _caps(**kw) -> Caps:
    # Explicit kwargs so tests don't depend on the dev's .env / shell env.
    base = dict(default_network=Network.PAPER, enable_mainnet=False)
    return Caps(**{**base, **kw})


def test_resolve_defaults_to_configured_network():
    assert resolve_network(None, _caps()) is Network.PAPER


def test_resolve_explicit_request_wins():
    assert resolve_network("testnet", _caps()) is Network.TESTNET


def test_resolve_rejects_unknown_network():
    with pytest.raises(ValueError):
        resolve_network("bogus", _caps())


def test_gate_noop_for_non_mainnet():
    enforce_mainnet_gate(Network.PAPER, _caps())
    enforce_mainnet_gate(Network.TESTNET, _caps())


def test_gate_blocks_mainnet_without_env_flag():
    with pytest.raises(MainnetGateError):
        enforce_mainnet_gate(Network.MAINNET, _caps(enable_mainnet=False), assume_yes=True)


def test_gate_allows_mainnet_with_flag_and_yes():
    enforce_mainnet_gate(Network.MAINNET, _caps(enable_mainnet=True), assume_yes=True)


def test_gate_requires_confirmation_without_yes():
    caps = _caps(enable_mainnet=True)
    enforce_mainnet_gate(Network.MAINNET, caps, confirm=lambda: True)
    with pytest.raises(MainnetGateError):
        enforce_mainnet_gate(Network.MAINNET, caps, confirm=lambda: False)
    with pytest.raises(MainnetGateError):
        enforce_mainnet_gate(Network.MAINNET, caps, confirm=None)
