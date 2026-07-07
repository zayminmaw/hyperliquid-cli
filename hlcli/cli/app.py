"""The `hl` Typer application — global flags + command-group wiring (PLAN.md §4).

Noun → verb taxonomy. Command groups live in `cli/commands/`; this module owns the
global callback (which parses the global flags into `GlobalState`) and assembles
the groups. Verbs not yet built are phase-labelled stubs so `hl --help` is fully
navigable from day one.

Note: no `from __future__ import annotations` here — Typer reads the real
annotation objects to build options.
"""

from typing import Optional

import typer

from hlcli.cli.commands import account, agent, asset, config, exec_, journal, markets, sentry, trade, tune
from hlcli.cli.context import GlobalState
from hlcli.core.config import get_caps
from hlcli.core.network import resolve_network

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="hl — trade on Hyperliquid. paper (default) → testnet → mainnet.",
)


@app.callback()
def main(
    ctx: typer.Context,
    network: Optional[str] = typer.Option(None, "--network", help="paper | testnet | mainnet"),
    account_alias: Optional[str] = typer.Option(None, "--account", help="account alias"),
    json_out: bool = typer.Option(False, "--json", help="machine-readable JSON output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="resolve everything but place no orders"),
    yes: bool = typer.Option(False, "-y", "--yes", help="skip confirmation prompts"),
) -> None:
    caps = get_caps()
    try:
        resolved = resolve_network(network, caps)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--network")
    ctx.obj = GlobalState(resolved, account_alias, json_out, dry_run, yes)


app.add_typer(account.app, name="account")
app.add_typer(markets.app, name="markets")
app.add_typer(asset.app, name="asset")
app.add_typer(trade.app, name="trade")
app.add_typer(exec_.app, name="exec")
app.add_typer(sentry.app, name="sentry")
app.add_typer(config.app, name="config")
app.add_typer(tune.app, name="tune")
app.add_typer(agent.app, name="agent")
app.add_typer(journal.app, name="journal")
