"""`hl config` — show resolved hard caps + the clamped tunable surface, and edit it.

`show` is read-only. `set`/`edit` change the **tunable** surface only
(`config/active_config.json`); hard caps live in `.env` and are off-limits here. The
tuner (`hl tune`) is the data-driven way to change the same surface — `set`/`edit` are
direct operator control over it, and write byte-identical, clamped files.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from pathlib import Path

import typer

from hlcli.cli.context import state_of
from hlcli.cli.output import emit, note
from hlcli.core.config import get_caps
from hlcli.core.config_schema import (
    get_field,
    load_tunable,
    save_tunable,
    set_field,
    tunable_keys,
)
from hlcli.core.llm import masked_api_key

app = typer.Typer(no_args_is_help=True, help="Configuration.")


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


@app.command("set")
def set_(
    ctx: typer.Context,
    key: str = typer.Argument(..., help="tunable field, dotted (e.g. sizing.enabled, trail.style)"),
    value: str = typer.Argument(..., help="new value; comma-list for allowed_regimes"),
) -> None:
    """Set one tunable field, then re-clamp on the way in.

    Hard caps (max_notional_per_trade, max_leverage, …) are refused — set those in `.env`.
    The written value is the *clamped* one, so it can never widen the box.
    """
    state = state_of(ctx)
    caps = get_caps()
    try:
        updated = set_field(load_tunable(), key, value)
    except KeyError:
        note(f"[red]'{key}' is not a tunable field.[/red] Hard caps live in .env. "
             f"Tunable keys: {', '.join(tunable_keys())}")
        raise typer.Exit(1)
    except ValueError as exc:
        note(f"[red]{exc}[/red]")
        raise typer.Exit(1)

    save_tunable(updated, caps.config_path)
    effective = get_field(load_tunable(), key)  # reloaded → the clamped, on-disk value
    emit(
        {"key": key, "requested": value, "effective": effective, "path": str(caps.config_path)},
        as_json=state.json_out, title="config set",
    )


@app.command("edit")
def edit(ctx: typer.Context) -> None:
    """Open the tunable config in $EDITOR; it is re-validated and clamped on save.

    A missing file is seeded with the current clamped surface first. Malformed JSON on
    save fails loudly (nothing bad reaches the order path); a valid-but-out-of-range edit
    is silently clamped back into range.
    """
    state = state_of(ctx)
    path = get_caps().config_path
    if not path.exists():
        save_tunable(load_tunable(), path)  # seed with the current clamped surface

    _launch_editor(path)
    cfg = load_tunable(path)  # validates + clamps (ConfigError on bad JSON → rendered)
    save_tunable(cfg, path)   # normalize the clamped result back to disk
    emit({"path": str(path), "status": "clamped_and_saved"}, as_json=state.json_out, title="config edit")


def _launch_editor(path: Path) -> None:
    """Open `path` in the user's editor. A module-level indirection so tests can stub it."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    subprocess.call([*shlex.split(editor), str(path)])
