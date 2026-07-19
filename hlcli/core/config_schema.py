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
import typing
from pathlib import Path

from pydantic import BaseModel, Field

from hlcli.core.config import get_caps

# Absolute clamp bounds. The gate additionally clamps the *resulting size* against
# the hard notional/leverage caps; these bounds stop absurd inputs at load time.
_RISK_PCT_MAX = 5.0
_MAX_CANDIDATES_CEILING = 50
_MAX_HOLD_CEILING = 10_080  # one week in minutes; an upper sanity bound on auto-expiry
_KNOWN_REGIMES = ("trend", "range")  # the gate's vocabulary; a tuner can't invent regimes
_TRAIL_STYLES = ("off", "atr", "percent")  # sentry trail methods; unknown style ⇒ default (off)


class RegimeGate(BaseModel):
    enabled: bool = True
    allowed_regimes: tuple[str, ...] = ("trend", "range")


class ConvictionSizing(BaseModel):
    """Maps the LLM's conviction (0–1) to a fraction of the max allowed size.

    **Disabled by default** (2026-07 audit, E1/E6): LLM conviction is an uncalibrated
    scalar until this book's own outcomes prove it predicts realized R — see the
    conviction-calibration table in `exec report`. Disabled ⇒ every gate-approved
    trade sizes at the full fixed-fractional target (fraction 1.0) and conviction is
    still logged, accumulating the calibration evidence that could re-enable this.
    """

    enabled: bool = False
    # Below this conviction the gate sizes to zero (treated as a skip).
    min_conviction: float = 0.3
    # Size scales between these fractions of the gate-permitted max as conviction
    # goes from `min_conviction` to 1.0. Never raises the ceiling.
    floor_fraction: float = 0.25
    ceil_fraction: float = 1.0


class TrailConfig(BaseModel):
    """Sentry 6a — deterministic in-trade management rules (PLAN.md §14).

    Distances are in R (the trade's initial risk, `|entry − initial_sl|`), so the
    rules stay coherent after the stop has already been ratcheted. Everything
    defaults OFF: an unconfigured install manages trades exactly as before.
    """

    style: str = "off"                # off | atr | percent — trailing-stop method
    atr_multiple: float = 2.0         # atr style: trail distance = multiple × ATR(14)
    trail_percent: float = 1.0        # percent style: distance = this % of the mark
    trail_start_r: float = 1.0        # trailing activates once unrealized ≥ this many R
    breakeven_trigger_r: float = 0.0  # move SL to entry ± buffer at this R; 0 disables
    breakeven_buffer_r: float = 0.05  # buffer past entry, in R
    scale_out_r: float = 0.0          # bank a fraction of the position at this R; 0 disables
    scale_out_fraction: float = 0.5   # fraction closed by the one-shot scale-out
    min_move_r: float = 0.1           # suppress SL moves smaller than this (churn guard)


class AgentConfig(BaseModel):
    """Agent-mode cadences (PLAN.md §15). Bounds keep a bad tune from spinning the
    loop hot (LLM spend) or stalling it past usefulness."""

    intake_poll_seconds: float = 5.0     # also the supervisor's tick granularity
    exec_interval_minutes: float = 5.0   # full intake pass; new files trigger one immediately
    sentry_interval_seconds: float = 60.0
    journal_narrative: bool = True       # the one daily opus reflection in `hl journal`
    reflection_inject: bool = True       # "recent lessons" block in decision/management context


class TunableConfig(BaseModel):
    risk_per_trade_pct: float = 0.5
    regime: RegimeGate = Field(default_factory=RegimeGate)
    sizing: ConvictionSizing = Field(default_factory=ConvictionSizing)
    max_candidates_per_pass: int = 5
    decision_temperature: float = 0.2  # low temp for the hot decision loop
    max_hold_minutes: int = 0  # auto-expire an open trade after this long; 0 disables
    trail: TrailConfig = Field(default_factory=TrailConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)


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

    t = cfg.trail
    dt = _DEFAULTS.trail
    trail = t.model_copy(
        update={
            "style": t.style if t.style in _TRAIL_STYLES else dt.style,
            "atr_multiple": _bound(t.atr_multiple, 0.5, 10.0, dt.atr_multiple),
            "trail_percent": _bound(t.trail_percent, 0.1, 20.0, dt.trail_percent),
            "trail_start_r": _bound(t.trail_start_r, 0.0, 10.0, dt.trail_start_r),
            "breakeven_trigger_r": _bound(t.breakeven_trigger_r, 0.0, 10.0, dt.breakeven_trigger_r),
            "breakeven_buffer_r": _bound(t.breakeven_buffer_r, 0.0, 0.9, dt.breakeven_buffer_r),
            "scale_out_r": _bound(t.scale_out_r, 0.0, 10.0, dt.scale_out_r),
            "scale_out_fraction": _bound(t.scale_out_fraction, 0.1, 0.9, dt.scale_out_fraction),
            "min_move_r": _bound(t.min_move_r, 0.0, 2.0, dt.min_move_r),
        }
    )

    a = cfg.agent
    da = _DEFAULTS.agent
    agent = a.model_copy(
        update={
            "intake_poll_seconds": _bound(a.intake_poll_seconds, 1.0, 60.0, da.intake_poll_seconds),
            "exec_interval_minutes": _bound(a.exec_interval_minutes, 0.5, 120.0, da.exec_interval_minutes),
            "sentry_interval_seconds": _bound(a.sentry_interval_seconds, 10.0, 3600.0, da.sentry_interval_seconds),
        }
    )

    return cfg.model_copy(
        update={
            "agent": agent,
            "risk_per_trade_pct": _bound(cfg.risk_per_trade_pct, 0.0, _RISK_PCT_MAX, _DEFAULTS.risk_per_trade_pct),
            "max_candidates_per_pass": int(
                _bound(cfg.max_candidates_per_pass, 1, _MAX_CANDIDATES_CEILING, _DEFAULTS.max_candidates_per_pass)
            ),
            "decision_temperature": _bound(cfg.decision_temperature, 0.0, 1.0, _DEFAULTS.decision_temperature),
            "max_hold_minutes": int(_bound(cfg.max_hold_minutes, 0, _MAX_HOLD_CEILING, _DEFAULTS.max_hold_minutes)),
            "regime": cfg.regime.model_copy(update={"allowed_regimes": regimes}),
            "trail": trail,
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


def save_tunable(cfg: TunableConfig, path: Path | None = None) -> Path:
    """Persist the tunable surface, clamped, as pretty JSON. The write mirrors the
    tuner's `promote` (same clamp-then-dump), so a manual `hl config set/edit` and a
    promotion produce byte-identical files."""
    path = path or get_caps().config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(clamp(cfg).model_dump_json(indent=2))
    return path


# --- manual field addressing (hl config set / edit) ---------------------------
#
# The tuner is the *primary* way the tunable surface changes, but an operator also
# needs direct control (turn a trail rule on, adjust risk before any cohort exists).
# Both write the same clamped file, so a manual edit is exactly as safe as a
# promotion. `set_field` only accepts paths that exist in the tunable model — a hard
# cap (which lives in .env) or a typo raises KeyError rather than silently no-op'ing.


def tunable_keys(cls: type[BaseModel] = TunableConfig, prefix: str = "") -> list[str]:
    """Every settable dotted leaf path, e.g. `risk_per_trade_pct`, `sizing.enabled`."""
    out: list[str] = []
    for name, field in cls.model_fields.items():
        ann = field.annotation
        dotted = f"{prefix}{name}"
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.extend(tunable_keys(ann, dotted + "."))
        else:
            out.append(dotted)
    return out


def _resolve_field_type(parts: list[str]) -> type:
    """The declared type of a dotted tunable path, or KeyError if it isn't one
    (a hard cap, a typo, or an attempt to descend into a scalar / assign a submodel)."""
    cls: type[BaseModel] = TunableConfig
    for i, part in enumerate(parts):
        fields = getattr(cls, "model_fields", {})
        if part not in fields:
            raise KeyError(".".join(parts))
        ann = fields[part].annotation
        if i == len(parts) - 1:
            if isinstance(ann, type) and issubclass(ann, BaseModel):
                raise KeyError(".".join(parts))  # a whole submodel isn't a leaf
            return ann
        if not (isinstance(ann, type) and issubclass(ann, BaseModel)):
            raise KeyError(".".join(parts))  # can't descend past a scalar
        cls = ann
    raise KeyError(".".join(parts))


def _coerce(raw: str, field_type: type) -> object:
    """Turn a CLI string into the field's declared type. Bool is checked by identity
    (it is not `int` here); comma lists fill tuple/list fields like allowed_regimes."""
    origin = typing.get_origin(field_type)
    if origin in (tuple, list):
        return tuple(s.strip() for s in raw.split(",") if s.strip())
    if field_type is bool:
        low = raw.strip().lower()
        if low in ("true", "1", "on", "yes", "y"):
            return True
        if low in ("false", "0", "off", "no", "n"):
            return False
        raise ValueError(f"expected a boolean (true/false), got {raw!r}")
    if field_type is int:
        return int(raw)
    if field_type is float:
        return float(raw)
    if field_type is str:
        return raw
    raise ValueError(f"cannot set a field of type {field_type!r}")


def set_field(cfg: TunableConfig, dotted_key: str, raw_value: str) -> TunableConfig:
    """A copy of `cfg` with one tunable leaf set from a CLI string, type-coerced.
    Not clamped here — `save_tunable` clamps on write and `load_tunable` on read."""
    parts = dotted_key.split(".")
    field_type = _resolve_field_type(parts)  # raises KeyError for non-tunable paths
    value = _coerce(raw_value, field_type)
    data = cfg.model_dump()
    node = data
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
    return TunableConfig.model_validate(data)


def get_field(cfg: TunableConfig, dotted_key: str) -> object:
    """Read a dotted tunable leaf's value (for echoing the effective, clamped result)."""
    node: object = cfg.model_dump()
    for part in dotted_key.split("."):
        node = node[part]  # type: ignore[index]
    return node
