"""Shared CLI context: parsed global flags + how to build an exchange from them.

Lives separately from `app.py` so command modules can import it without a cycle.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import typer

from hlcli.accounts.keystore import Keystore
from hlcli.accounts.store import Account, AccountType, open_store
from hlcli.core.config import Caps, get_caps
from hlcli.core.config_schema import TunableConfig, load_tunable
from hlcli.core.network import enforce_mainnet_gate
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.exchange.factory import build_exchange
from hlcli.exchange.paper import PaperExchange
from hlcli.state.store import StateStore, open_state


@dataclass
class GlobalState:
    """Parsed global flags, stashed on the Typer context for every command."""

    network: Network
    account: Optional[str]
    json_out: bool
    dry_run: bool
    yes: bool


def state_of(ctx: typer.Context) -> GlobalState:
    return ctx.obj


def typed_confirm(network: Network) -> Callable[[], bool]:
    """Prompt the user to type the network name — the mainnet typed confirmation."""

    def confirm() -> bool:
        return typer.prompt(f"Type '{network.value}' to confirm").strip() == network.value

    return confirm


def resolve_account(state: GlobalState) -> Account | None:
    """The account to act as on the current network (explicit alias or the default)."""
    if state.network is Network.PAPER:
        return None
    return open_store(get_caps()).resolve(state.account, state.network)


def open_env(state: GlobalState, *, for_write: bool) -> tuple[Exchange, StateStore, Caps, TunableConfig]:
    """The executor-side working set (exchange, state, caps, tunable) for the current
    network. Shared by `exec`, `sentry`, and `agent` so paper wiring stays identical."""
    caps = get_caps()
    store = open_state(caps, state.network)
    tunable = load_tunable()
    if state.network is Network.PAPER:
        exchange: Exchange = PaperExchange(caps.starting_equity, state=store)
    else:
        exchange = build_for(state, for_write=for_write)
    return exchange, store, caps, tunable


def build_for(state: GlobalState, *, for_write: bool) -> Exchange:
    """Construct the backend for the current network/account.

    The agent key is loaded ONLY for write actions — reads never touch it, keeping
    the key on disk for `positions`/`orders`/`balances`. Mainnet writes pass the gate.
    """
    caps = get_caps()
    if for_write:
        enforce_mainnet_gate(
            state.network, caps, assume_yes=state.yes, confirm=typed_confirm(state.network)
        )

    if state.network is Network.PAPER:
        return build_exchange(Network.PAPER, caps)

    account = resolve_account(state)
    if account is None:
        raise typer.BadParameter(
            f"no account for {state.network}; add one with `hl account add`.",
            param_hint="--account",
        )

    agent_key = None
    if for_write and account.type is AccountType.TRADE and account.key_ref:
        agent_key = Keystore(caps.data_dir / "keys").load(account.key_ref)
    return build_exchange(state.network, caps, account=account, agent_key=agent_key)
