"""`hl config` — show resolved hard caps + the clamped tunable surface."""

from __future__ import annotations

import typer

from hlcli.cli.context import state_of
from hlcli.cli.output import emit
from hlcli.cli.stubs import stub_command
from hlcli.core.config import get_caps
from hlcli.core.config_schema import load_tunable
from hlcli.core.llm import masked_api_key

app = typer.Typer(no_args_is_help=True, help="Configuration.")

# Editing the tunable surface is the tuner's job (Phase 4).
for _cmd, _phase in {"set": 4, "edit": 4}.items():
    app.command(name=_cmd)(stub_command("config", _cmd, _phase))


@app.command("show")
def show(ctx: typer.Context) -> None:
    state = state_of(ctx)
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
            "anthropic_api_key": masked_api_key() or "not set",
            "risk_per_trade_pct": tunable.risk_per_trade_pct,
            "regime_enabled": tunable.regime.enabled,
            "conviction_min": tunable.sizing.min_conviction,
        },
        as_json=state.json_out, title="config",
    )
