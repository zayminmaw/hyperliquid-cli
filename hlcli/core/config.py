"""Hard caps — the off-limits layer (PLAN.md §9).

These come from `.env` and are **off-limits to the LLM and the tuner**. Nothing
here is ever rewritten by self-tuning. They define the box the order path must
stay inside: notional/leverage ceilings, the loss limit, allowed coins, the
mainnet gate, and the model names + token budgets.

The split between this (hard) and `config_schema` (tunable, clamped) is what makes
self-tuning safe — a tunable value is always clamped against these before it can
reach an order.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from hlcli.core.types import Network


class Caps(BaseSettings):
    """Hard caps and environment gates, read from `HL_*` env vars / `.env`."""

    model_config = SettingsConfigDict(
        env_prefix="HL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- network + mainnet gate ---
    default_network: Network = Network.PAPER
    enable_mainnet: bool = False  # HL_ENABLE_MAINNET=1 is one of three mainnet conditions

    # --- paths ---
    data_dir: Path = Path.home() / ".hyperliquid-cli"
    config_path: Path = Path("config/active_config.json")

    # --- risk ceilings (the order path can never exceed these) ---
    starting_equity: float = 10_000.0
    max_notional_per_trade: float = 1_000.0
    max_concurrent_positions: int = 3
    daily_loss_limit_pct: float = 5.0
    max_leverage: float = 3.0
    rr_floor: float = 1.5
    max_signal_age_minutes: int = 30
    # How many times the executor re-checks a candidate the LLM said to WAIT on before
    # giving up. 0 disables follow-ups (a `wait` becomes a terminal reject, as before).
    followup_max_attempts: int = 3

    # comma-separated in env (HL_ALLOWED_COINS="BTC,ETH,SOL"); use `.coins`
    allowed_coins: str = "BTC,ETH,SOL"

    # --- graduation checklist (mainnet readiness; risk policy, off-limits to the tuner) ---
    graduation_min_trades: int = 20
    graduation_min_days: int = 7
    graduation_min_expectancy: float = 0.0  # mean R-multiple must clear this

    # --- models + token budgets (configurable, but a hard cap on spend/choice) ---
    decision_model: str = "claude-sonnet-4-6"
    decision_max_tokens: int = 1024
    tuner_model: str = "claude-opus-4-8"
    tuner_max_tokens: int = 4096

    @property
    def coins(self) -> tuple[str, ...]:
        """Allowed coins as an upper-cased tuple, parsed from the CSV env value."""
        return tuple(c.strip().upper() for c in self.allowed_coins.split(",") if c.strip())


@lru_cache
def get_caps() -> Caps:
    """Process-wide hard caps. Cached so `.env` is read once."""
    return Caps()
