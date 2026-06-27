"""`hl account` — multi-account management + read-only portfolio views (PLAN.md §8)."""

from __future__ import annotations

import typer

from hlcli.accounts.keystore import Keystore, agent_address
from hlcli.accounts.store import Account, AccountType, open_store
from hlcli.cli.context import build_for, state_of
from hlcli.cli.output import emit, emit_rows, note
from hlcli.cli.watch import watch_rows
from hlcli.core.config import get_caps
from hlcli.core.types import Network

app = typer.Typer(no_args_is_help=True, help="Accounts: add/list/select + positions/orders/balances.")


@app.command("add")
def add(
    ctx: typer.Context,
    alias: str = typer.Argument(..., help="unique account alias"),
    address: str = typer.Option(..., "--address", help="main account address being traded"),
    read_only: bool = typer.Option(False, "--read-only", help="monitor-only account (no key)"),
) -> None:
    """Add an account for the current `--network`. Trade accounts prompt for the agent key."""
    state = state_of(ctx)
    if state.network is Network.PAPER:
        raise typer.BadParameter("paper needs no account; use --network testnet|mainnet.")

    caps = get_caps()
    acct_type = AccountType.READ_ONLY if read_only else AccountType.TRADE
    key_ref = None

    if acct_type is AccountType.TRADE:
        # Read the key off the prompt — never a CLI arg (shell history) and never logged.
        private_key = typer.prompt("Agent private key", hide_input=True)
        derived = agent_address(private_key)
        key_ref = Keystore(caps.data_dir / "keys").save(alias, private_key)
        note(f"[dim]agent wallet: {derived} (approve this agent for {address} on Hyperliquid)[/dim]")

    store = open_store(caps)
    account = store.add(
        Account(alias=alias, address=address, network=state.network, type=acct_type, key_ref=key_ref)
    )
    emit(
        {"alias": account.alias, "address": account.address, "network": account.network.value,
         "type": account.type.value, "default": account.is_default},
        as_json=state.json_out, title="account added",
    )


@app.command("ls")
def ls(ctx: typer.Context, all_networks: bool = typer.Option(False, "--all", help="all networks")) -> None:
    state = state_of(ctx)
    network = None if all_networks else state.network
    accounts = open_store(get_caps()).list(network)
    emit_rows(
        [{"alias": a.alias, "network": a.network.value, "type": a.type.value,
          "address": a.address, "default": "*" if a.is_default else ""} for a in accounts],
        as_json=state.json_out, title="accounts",
    )


@app.command("set-default")
def set_default(ctx: typer.Context, alias: str = typer.Argument(...)) -> None:
    state = state_of(ctx)
    account = open_store(get_caps()).set_default(alias)
    note(f"default for {account.network.value} → [green]{account.alias}[/green]")


@app.command("remove")
def remove(ctx: typer.Context, alias: str = typer.Argument(...)) -> None:
    state = state_of(ctx)
    caps = get_caps()
    account = open_store(caps).remove(alias)
    if account.key_ref:
        Keystore(caps.data_dir / "keys").delete(account.key_ref)
    note(f"removed [yellow]{alias}[/yellow]")


@app.command("positions")
def positions(ctx: typer.Context, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)

    def rows() -> list[dict]:
        return [{"coin": p.coin, "side": p.side.value, "size": p.size,
                 "entry": p.entry_price, "uPnL": p.unrealized_pnl} for p in exchange.get_positions()]

    if watch and not state.json_out:
        watch_rows(rows, title="positions")
        return
    emit_rows(rows(), as_json=state.json_out, title="positions")


@app.command("orders")
def orders(ctx: typer.Context, watch: bool = typer.Option(False, "-w", "--watch")) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)

    def rows() -> list[dict]:
        return [{"coin": o.coin, "oid": o.oid, "side": o.side.value, "size": o.size,
                 "price": o.price, "reduceOnly": o.reduce_only} for o in exchange.get_open_orders()]

    if watch and not state.json_out:
        watch_rows(rows, title="open orders")
        return
    emit_rows(rows(), as_json=state.json_out, title="open orders")


@app.command("balances")
def balances(ctx: typer.Context) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)
    emit({"network": state.network.value, "equity": exchange.equity()},
         as_json=state.json_out, title="balances")


@app.command("portfolio")
def portfolio(ctx: typer.Context) -> None:
    state = state_of(ctx)
    exchange = build_for(state, for_write=False)
    positions = exchange.get_positions()
    emit(
        {"network": state.network.value, "equity": exchange.equity(),
         "open_positions": len(positions),
         "unrealized_pnl": round(sum(p.unrealized_pnl for p in positions), 4)},
        as_json=state.json_out, title="portfolio",
    )
