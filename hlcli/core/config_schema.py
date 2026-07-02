"""Tunable surface — the clamped layer (PLAN.md §9).

`config/active_config.json` holds the values the self-tuner is allowed to change:
the regime gate, risk-per-trade, the conviction→size mapping, decision-prompt
knobs. It is **loaded and clamped in code** so a bad (or maliciously tuned) value
can never reach the order path. Missing file → safe defaults.

The clamp is the safety contract: every field is bounded into a sane range here,
*before* the value is ever used for sizing or the gate.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

from pydantic import BaseModel, Field

from hlcli.core.config import get_caps

# Absolute clamp bounds. The gate additionally clamps the *resulting size* against
# the hard notional/leverage caps; these bounds stop absurd inputs at load time.
_RISK_PCT_MAX = 5.0
_MAX_CANDIDATES_CEILING = 50
_MAX_HOLD_CEILING = 10_080  # one week in minutes; an upper sanity bound on auto-expiry
_KNOWN_REGIMES = ("trend", "range")  # the gate's vocabulary; a tuner can't invent regimes


class RegimeGate(BaseModel):
    enabled: bool = True
    allowed_regimes: tuple[str, ...] = ("trend", "range")


class ConvictionSizing(BaseModel):
    """Maps the LLM's conviction (0–1) to a fraction of the max allowed size."""

    # Below this conviction the gate sizes to zero (treated as a skip).
    min_conviction: float = 0.3
    # Size scales between these fractions of the gate-permitted max as conviction
    # goes from `min_conviction` to 1.0. Never raises the ceiling.
    floor_fraction: float = 0.25
    ceil_fraction: float = 1.0


class TunableConfig(BaseModel):
    risk_per_trade_pct: float = 0.5
    regime: RegimeGate = Field(default_factory=RegimeGate)
    sizing: ConvictionSizing = Field(default_factory=ConvictionSizing)
    max_candidates_per_pass: int = 5
    decision_temperature: float = 0.2  # low temp for the hot decision loop
    max_hold_minutes: int = 0  # auto-expire an open trade after this long; 0 disables


class ConfigError(RuntimeError):
    """The active config file exists but could not be parsed."""


def _bound(value: float, lo: float, hi: float, default: float) -> float:
    """Clamp into [lo, hi]. A non-finite value would slide through min/max as the
    UPPER bound (NaN compares false everywhere) — it's garbage, so use the field's
    safe default instead of the widest setting."""
    if not math.isfinite(value):
        return default
    return max(lo, min(hi, value))


_DEFAULTS = TunableConfig()


def clamp(cfg: TunableConfig) -> TunableConfig:
    """Bound every tunable field into its safe range. Idempotent."""
    s = cfg.sizing
    d = _DEFAULTS.sizing
    floor = _bound(s.floor_fraction, 0.0, 1.0, d.floor_fraction)
    ceil = _bound(s.ceil_fraction, 0.0, 1.0, d.ceil_fraction)
    floor = min(floor, ceil)  # floor can never exceed ceil

    regimes = tuple(r for r in cfg.regime.allowed_regimes if r in _KNOWN_REGIMES) or _KNOWN_REGIMES

    return cfg.model_copy(
        update={
            "risk_per_trade_pct": _bound(cfg.risk_per_trade_pct, 0.0, _RISK_PCT_MAX, _DEFAULTS.risk_per_trade_pct),
            "max_candidates_per_pass": int(
                _bound(cfg.max_candidates_per_pass, 1, _MAX_CANDIDATES_CEILING, _DEFAULTS.max_candidates_per_pass)
            ),
            "decision_temperature": _bound(cfg.decision_temperature, 0.0, 1.0, _DEFAULTS.decision_temperature),
            "max_hold_minutes": int(_bound(cfg.max_hold_minutes, 0, _MAX_HOLD_CEILING, _DEFAULTS.max_hold_minutes)),
            "regime": cfg.regime.model_copy(update={"allowed_regimes": regimes}),
            "sizing": s.model_copy(
                update={
                    "min_conviction": _bound(s.min_conviction, 0.0, 1.0, d.min_conviction),
                    "floor_fraction": floor,
                    "ceil_fraction": ceil,
                }
            ),
        }
    )


def load_tunable(path: Path | None = None) -> TunableConfig:
    """Load + clamp the tunable config. Missing file → clamped defaults.

    A malformed file is surfaced rather than silently ignored — running on a
    config the user *thinks* is active would be worse than failing loudly.
    """
    path = path or get_caps().config_path
    if not path.exists():
        return clamp(TunableConfig())

    try:
        raw = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path} is not valid JSON: {exc}") from exc

    return clamp(TunableConfig.model_validate(raw))
