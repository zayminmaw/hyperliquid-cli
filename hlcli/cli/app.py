"""The `hl` Typer application — command groups + global flags (PLAN.md §4).

Noun → verb taxonomy. Phase 0 ships working `exec once` (no-op pass) and
`config show`; every other verb is a clearly-labelled stub for its phase so
`hl --help` is fully navigable from day one.

Note: no `from __future__ import annotations` here — Typer reads the real
annotation objects to build options.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Optional

import typer

from hlcli.cli.output import emit, note
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.core.network import enforce_mainnet_gate, resolve_network
from hlcli.core.types import Network
from hlcli.exchange.factory import build_exchange
from hlcli.executor.runner import run_once


@dataclass
class GlobalState:
    """Parsed global flags, stashed on the Typer context for every command."""

    network: Network
    account: Optional[str]
    json_out: bool
    dry_run: bool
    yes: bool


app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    help="hl — trade on Hyperliquid. paper (default) → testnet → mainnet.",
)


@app.callback()
def main(
    ctx: typer.Context,
    network: Optional[str] = typer.Option(None, "--network", help="paper | testnet | mainnet"),
    account: Optional[str] = typer.Option(None, "--account", help="account alias"),
    json_out: bool = typer.Option(False, "--json", help="machine-readable JSON output"),
    dry_run: bool = typer.Option(False, "--dry-run", help="resolve everything but place no orders"),
    yes: bool = typer.Option(False, "-y", "--yes", help="skip confirmation prompts"),
) -> None:
    caps = get_caps()
    try:
        resolved = resolve_network(network, caps)
    except ValueError as exc:
        raise typer.BadParameter(str(exc), param_hint="--network")
    ctx.obj = GlobalState(resolved, account, json_out, dry_run, yes)


# --- stub factory: one place that says "this verb isn't built yet" ------------

def _stub(group: str, command: str, phase: int) -> Callable[[typer.Context], None]:
    def run(ctx: typer.Context) -> None:
        state: GlobalState = ctx.obj
        if state.json_out:
            emit(
                {"error": "not_implemented", "command": f"{group} {command}", "phase": phase},
                as_json=True,
            )
        else:
            note(f"[yellow]`hl {group} {command}` is not built yet — arrives in Phase {phase}.[/yellow]")
        raise typer.Exit(1)

    return run


def _stub_group(group: str, help_text: str, commands: dict[str, int]) -> typer.Typer:
    grp = typer.Typer(no_args_is_help=True, help=help_text)
    for command, phase in commands.items():
        grp.command(name=command)(_stub(group, command, phase))
    return grp


# --- command groups -----------------------------------------------------------

app.add_typer(
    _stub_group("account", "Accounts: add/list/select, positions/orders/balances (Phase 1).", {
        "add": 1, "ls": 1, "set-default": 1, "remove": 1,
        "positions": 1, "orders": 1, "balances": 1, "portfolio": 1,
    }),
    name="account",
)
app.add_typer(_stub_group("markets", "Market data (Phase 1).", {"ls": 1, "prices": 1}), name="markets")
app.add_typer(_stub_group("asset", "Per-asset price/book; -w to watch (Phase 1).", {"price": 1, "book": 1}), name="asset")
app.add_typer(
    _stub_group("trade", "Manual (Mode A) trading (Phase 1).", {
        "order": 1, "cancel": 1, "cancel-all": 1, "set-leverage": 1,
    }),
    name="trade",
)
app.add_typer(
    _stub_group("tune", "Self-tuning: propose → approve (Phase 4).", {
        "run": 4, "diff": 4, "promote": 4, "history": 4,
    }),
    name="tune",
)

exec_app = typer.Typer(no_args_is_help=True, help="LLM executor (Mode B).")
for _cmd, _phase in {"propose": 2, "run": 2, "shadow": 3, "status": 1, "report": 1, "breaker": 2}.items():
    exec_app.command(name=_cmd)(_stub("exec", _cmd, _phase))


@exec_app.command("once")
def exec_once(ctx: typer.Context) -> None:
    """One full executor pass: intake → enrich → decision → gate → fire → monitor."""
    state: GlobalState = ctx.obj
    caps = get_caps()
    enforce_mainnet_gate(
        state.network, caps, assume_yes=state.yes, confirm=_typed_confirm(state.network)
    )
    exchange = build_exchange(state.network, caps)
    summary = run_once(exchange, dry_run=state.dry_run)
    emit(summary.model_dump(), as_json=state.json_out, title="exec once")


app.add_typer(exec_app, name="exec")

config_app = typer.Typer(no_args_is_help=True, help="Configuration (Phase 0: show).")
for _cmd, _phase in {"set": 1, "edit": 1}.items():
    config_app.command(name=_cmd)(_stub("config", _cmd, _phase))


@config_app.command("show")
def config_show(ctx: typer.Context) -> None:
    """Show resolved hard caps + the clamped tunable surface."""
    state: GlobalState = ctx.obj
    caps = get_caps()
    tunable = load_tunable()
    emit(
        {
            "network": state.network.value,
            "enable_mainnet": caps.enable_mainnet,
            "starting_equity": caps.starting_equity,
            "max_notional_per_trade": caps.max_notional_per_trade,
            "max_concurrent_positions": caps.max_concurrent_positions,
            "daily_loss_limit_pct": caps.daily_loss_limit_pct,
            "max_leverage": caps.max_leverage,
            "rr_floor": caps.rr_floor,
            "max_signal_age_minutes": caps.max_signal_age_minutes,
            "allowed_coins": ",".join(caps.coins),
            "decision_model": caps.decision_model,
            "tuner_model": caps.tuner_model,
            "risk_per_trade_pct": tunable.risk_per_trade_pct,
            "regime_enabled": tunable.regime.enabled,
            "conviction_min": tunable.sizing.min_conviction,
        },
        as_json=state.json_out,
        title="config",
    )


app.add_typer(config_app, name="config")


def _typed_confirm(network: Network) -> Callable[[], bool]:
    """Prompt the user to type the network name (the mainnet typed confirmation)."""

    def confirm() -> bool:
        typed = typer.prompt(f"Type '{network.value}' to confirm")
        return typed.strip() == network.value

    return confirm
