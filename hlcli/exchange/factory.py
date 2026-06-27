"""Build the right exchange backend for a resolved network.

Phase 0 only ships the paper backend. The live (testnet/mainnet) backend lands in
Phase 1 and is lazy-imported there so paper + tests never need the signing libs.
"""

from __future__ import annotations

from hlcli.core.config import Caps
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.exchange.paper import PaperExchange


def build_exchange(network: Network, caps: Caps) -> Exchange:
    if network is Network.PAPER:
        return PaperExchange(starting_equity=caps.starting_equity)
    raise NotImplementedError(f"the {network} backend arrives in Phase 1")
