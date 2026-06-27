"""Build the right exchange backend for a resolved network + account.

Paper needs nothing. Live networks need an account address; trading additionally
needs the agent key (a read-only account passes `agent_key=None`).
"""

from __future__ import annotations

from hlcli.accounts.store import Account
from hlcli.core.config import Caps
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.exchange.paper import PaperExchange


def build_exchange(
    network: Network,
    caps: Caps,
    *,
    account: Account | None = None,
    agent_key: str | None = None,
) -> Exchange:
    if network is Network.PAPER:
        return PaperExchange(starting_equity=caps.starting_equity)

    if account is None:
        raise ValueError(f"{network} needs an account — add one with `hl account add` or pass --account.")

    # Imported here so paper/tests never load the live backend's module graph.
    from hlcli.exchange.hyperliquid import HyperliquidExchange

    return HyperliquidExchange(
        network, account_address=account.address, agent_key=agent_key
    )
