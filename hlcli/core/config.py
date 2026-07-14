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
from typing import Literal

from pydantic import model_validator
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
    # Relative paths resolve against data_dir (see below), so `hl` behaves the same
    # from any working directory; set an absolute HL_CONFIG_PATH to opt out.
    config_path: Path = Path("config/active_config.json")

    # --- agent mode (PLAN.md §15) ---
    # Producers drop candidate-batch JSON files here (per-network subdir appended).
    agent_intake_dir: Path | None = None  # default: <data_dir>/intake
    agent_daily_utc: str = "00:10"        # HH:MM UTC — when the daily jobs run
    # Reflection memory bounds (§15.4) — how much distilled lesson text may ride
    # into the decision/management context. Hard caps: the inject can't bloat.
    agent_reflect_inject_max: int = 3
    agent_reflect_max_chars: int = 240

    @model_validator(mode="after")
    def _anchor_config_path(self) -> "Caps":
        if not self.config_path.is_absolute():
            self.config_path = self.data_dir / self.config_path
        if self.agent_intake_dir is not None and not self.agent_intake_dir.is_absolute():
            self.agent_intake_dir = self.data_dir / self.agent_intake_dir
        return self

    # --- risk ceilings (the order path can never exceed these) ---
    starting_equity: float = 10_000.0
    max_notional_per_trade: float = 1_000.0
    max_concurrent_positions: int = 3
    daily_loss_limit_pct: float = 5.0
    max_leverage: float = 3.0
    rr_floor: float = 1.5
    max_signal_age_minutes: int = 30
    # Exchange-enforced floor (Hyperliquid rejects orders under $10 notional) — the gate
    # rejects early with a clear reason instead of a late exchange error (audit X-2).
    min_order_notional: float = 10.0
    # Worst acceptable entry fill vs the mid, in percent (audit X-1). The live entry is
    # an IOC limit at mid ± this — normally fills like a market order, but refuses a
    # fill worse than the cap (leverage multiplies slippage into margin). Entries only:
    # closes must fill and stay wide.
    max_entry_slippage_pct: float = 0.3
    # How many times the executor re-checks a candidate the LLM said to WAIT on before
    # giving up. 0 disables follow-ups (a `wait` becomes a terminal reject, as before).
    followup_max_attempts: int = 3

    # comma-separated in env (HL_ALLOWED_COINS="BTC,ETH,SOL"); use `.coins`
    allowed_coins: str = "BTC,ETH,SOL"

    # --- sentry churn caps (PLAN.md §14 — enforced in code, not prompt; "day" = rolling 24h) ---
    sentry_eval_interval_minutes: float = 15.0       # a position is LLM-evaluated at most this often
    sentry_min_action_interval_minutes: float = 30.0  # per-position cooldown after any applied action
    sentry_max_actions_per_position_per_day: int = 4
    sentry_max_llm_calls_per_day: int = 200           # backstop across all positions
    sentry_opposing_window_minutes: float = 120.0     # no extend_tp ↔ reduce flip-flops inside this
    # ADD (6d) — the one risk-increasing action; pyramid rules are hard policy:
    sentry_add_min_r: float = 1.0            # adds only to winners at/above this unrealized R
    # Default 0 = ADD disabled (2026-07 audit, L-3): the lone risk-*increasing* lever stays
    # off until the book has graduated; raising this is a deliberate risk decision.
    sentry_max_adds_per_position: int = 0    # cap per open position (resets when the coin is flat)

    # --- graduation checklist (mainnet readiness; risk policy, off-limits to the tuner) ---
    graduation_min_trades: int = 20
    graduation_min_days: int = 7
    graduation_min_expectancy: float = 0.0  # mean R-multiple must clear this

    # --- reconciliation response (2026-07 audit, O-2) ---
    # What an executor pass does about an exchange position the ledger doesn't know:
    # "alert" (default) pages a human; "adopt" additionally books stop-protected
    # positions into the ledger via the sentry adopt path (never invents a stop —
    # stopless positions still only alert). Flattening stays a manual decision:
    # auto-closing could kill a position the human opened on purpose.
    reconcile_action: Literal["alert", "adopt"] = "alert"

    # --- decision source (2026-07 audit, L-2) ---
    # Which arbiter judges candidates: "llm" (the decision model) or "rule" (the
    # deterministic act-on-every-gate-valid-setup baseline — no LLM call, no key).
    # A hard cap, not a tunable: the A/B control must be off-limits to the tuner.
    # A/B procedure: run each source in shadow against the same intake under separate
    # HL_DATA_DIRs, then compare `exec report` expectancy at the graduation sample.
    decision_source: Literal["llm", "rule"] = "llm"

    # --- models + token budgets (configurable, but a hard cap on spend/choice) ---
    decision_model: str = "claude-sonnet-4-6"
    decision_max_tokens: int = 1024
    tuner_model: str = "claude-opus-4-8"
    tuner_max_tokens: int = 4096
    journal_model: str = "claude-opus-4-8"  # daily narrative — out-of-path, one call/day
    journal_max_tokens: int = 2048

    @property
    def coins(self) -> tuple[str, ...]:
        """Allowed coins as an upper-cased tuple, parsed from the CSV env value."""
        return tuple(c.strip().upper() for c in self.allowed_coins.split(",") if c.strip())


@lru_cache
def get_caps() -> Caps:
    """Process-wide hard caps. Cached so `.env` is read once."""
    return Caps()
