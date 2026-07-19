"""Config tuner (PLAN.md §10) — out of the order path, propose → approve.

Reads resolved-trade cohorts and asks `claude-sonnet-5` to propose edits to the
*tunable surface* (risk %, regime gate, conviction→size mapping, hold/expiry).
**Sample-gated**: with no eligible cohort the model is never called. The proposal
is clamped before it is returned, and again on load — a tuned value can never
widen the hard-cap box.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import ValidationError

from hlcli.core.config import Caps
from hlcli.core.config_schema import TunableConfig, clamp
from hlcli.core.llm import make_client
from hlcli.state.store import StateStore
from hlcli.tuner.stats import MIN_COHORT_SAMPLES, Cohort, cohorts, management_cohorts, summary

# Strict mode can't encode numeric ranges, so the clamp bounds live in the field
# descriptions — the model's only channel for knowing them. A proposal outside a
# bound gets silently clamped, which is no longer the config the model argued for,
# so keep these in sync with `config_schema.clamp`.
CONFIG_TOOL = {
    "name": "submit_config",
    "description": "Submit the full proposed tunable config. Values outside the stated bounds are clamped on load — propose within them.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "risk_per_trade_pct": {"type": "number", "description": "Percent of equity risked per trade (fixed-fractional sizing). Clamped to [0.0, 5.0]."},
            "regime": {
                "type": "object",
                "description": "The deterministic regime gate: when enabled, a candidate whose code-computed regime is not in allowed_regimes is rejected before sizing.",
                "properties": {
                    "enabled": {"type": "boolean", "description": "Whether the regime gate runs at all."},
                    "allowed_regimes": {"type": "array", "items": {"type": "string"}, "description": "Subset of ['trend', 'range'] — unknown names are discarded on load."},
                },
                "required": ["enabled", "allowed_regimes"],
                "additionalProperties": False,
            },
            "sizing": {
                "type": "object",
                "description": "Maps the decision model's conviction (0-1) to a fraction of the gate-permitted max size. Never raises the hard-cap ceiling.",
                "properties": {
                    "min_conviction": {"type": "number", "description": "Conviction below this sizes to zero (an effective skip). Clamped to [0.0, 1.0]."},
                    "floor_fraction": {"type": "number", "description": "Size fraction at conviction == min_conviction. Clamped to [0.0, 1.0] and never above ceil_fraction."},
                    "ceil_fraction": {"type": "number", "description": "Size fraction at conviction == 1.0. Clamped to [0.0, 1.0]."},
                },
                "required": ["min_conviction", "floor_fraction", "ceil_fraction"],
                "additionalProperties": False,
            },
            "max_candidates_per_pass": {"type": "integer", "description": "Fresh candidates pulled per executor pass. Clamped to [1, 50]."},
            "decision_temperature": {"type": "number", "description": "Sampling temperature for the order-path decision model (ignored on models that reject sampling params). Clamped to [0.0, 1.0]."},
            "max_hold_minutes": {"type": "integer", "description": "Auto-expire an open trade after this many minutes; 0 disables expiry. Clamped to [0, 10080] (one week)."},
            "trail": {
                "type": "object",
                "description": "Sentry in-trade management (the deterministic 6a rules). Tune from management_cohorts: do trailed / scaled trades out-perform ones left on the initial stop? Distances are in R (the trade's initial risk). Everything off by default.",
                "properties": {
                    "style": {"type": "string", "description": "Trailing-stop method: 'off', 'atr', or 'percent'. Any other value resets to 'off' on load."},
                    "atr_multiple": {"type": "number", "description": "atr style: trail distance = this x ATR(14). Clamped to [0.5, 10.0]."},
                    "trail_percent": {"type": "number", "description": "percent style: trail distance = this percent of the mark. Clamped to [0.1, 20.0]."},
                    "trail_start_r": {"type": "number", "description": "Trailing activates once unrealized >= this many R. Clamped to [0.0, 10.0]."},
                    "breakeven_trigger_r": {"type": "number", "description": "Move the stop to entry +/- buffer at this R; 0 disables. Clamped to [0.0, 10.0]."},
                    "breakeven_buffer_r": {"type": "number", "description": "Buffer past entry for the breakeven stop, in R. Clamped to [0.0, 0.9]."},
                    "scale_out_r": {"type": "number", "description": "Bank a partial at this R; 0 disables scale-out. Clamped to [0.0, 10.0]."},
                    "scale_out_fraction": {"type": "number", "description": "Fraction of the position closed by the one-shot scale-out. Clamped to [0.1, 0.9]."},
                    "min_move_r": {"type": "number", "description": "Suppress stop moves smaller than this (in R) — a churn guard. Clamped to [0.0, 2.0]."},
                },
                "required": [
                    "style", "atr_multiple", "trail_percent", "trail_start_r", "breakeven_trigger_r",
                    "breakeven_buffer_r", "scale_out_r", "scale_out_fraction", "min_move_r",
                ],
                "additionalProperties": False,
            },
        },
        "required": [
            "risk_per_trade_pct", "regime", "sizing",
            "max_candidates_per_pass", "decision_temperature", "max_hold_minutes", "trail",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "You tune the strategy config of a crypto-futures executor from its own resolved-trade "
    "record. You are out of the order path: your proposal is reviewed by a human and clamped "
    "to safe bounds before it can ever take effect, so propose the config you believe maximizes "
    "expectancy.\n\n"
    "Favor regimes/conviction-buckets/coins with positive expectancy (avg_r) and adequate sample "
    "size; pull risk and size away from cohorts that lose. For the trail (in-trade management) "
    "config, read management_cohorts: if trades whose stop trailed or that scaled out under-perform "
    "ones left on the initial stop, ease off that lever; if they out-perform, lean into it. Let the "
    "evidence set the step size: a well-sampled cohort justifies a large move, a thin one only a "
    "small move. Do not anchor to the current values, and do not move beyond what the cohorts "
    "support. Always answer via submit_config with the FULL config."
)


@dataclass
class ConfigProposal:
    proposed: TunableConfig | None  # None = nothing proposed
    note: str  # "ok" | "no_eligible_cohort" | "invalid_output"
    cohorts: list[Cohort]


def propose_config(
    state: StateStore,
    caps: Caps,
    tunable: TunableConfig,
    *,
    client=None,
    min_samples: int = MIN_COHORT_SAMPLES,
) -> ConfigProposal:
    trades = state.resolved_trades()
    eligible = cohorts(trades, min_samples=min_samples)
    if not eligible:
        return ConfigProposal(None, "no_eligible_cohort", [])  # model is not called

    client = client or make_client()
    response = client.messages.create(
        model=caps.tuner_model,
        max_tokens=caps.tuner_max_tokens,
        system=SYSTEM_PROMPT,
        tools=[CONFIG_TOOL],
        tool_choice={"type": "tool", "name": "submit_config"},
        messages=[{"role": "user", "content": _prompt(
            tunable, eligible, summary(trades), management_cohorts(trades))}],
    )

    proposed = _validate(_tool_payload(response), tunable)
    if proposed is None:
        return ConfigProposal(None, "invalid_output", eligible)
    return ConfigProposal(proposed, "ok", eligible)


def _prompt(current: TunableConfig, eligible: list[Cohort], overall: dict, management: list[dict]) -> str:
    return json.dumps({
        "current_config": current.model_dump(),
        "overall": overall,
        "cohorts": [c.__dict__ for c in eligible],
        "management_cohorts": management,
    }, indent=2)


def _validate(payload: object, current: TunableConfig) -> TunableConfig | None:
    """Overlay the proposed fields onto the *current* config, then clamp. Merging (rather
    than validating the bare payload) preserves fields the tool schema doesn't expose — e.g.
    `agent` cadences — which a bare validate would silently reset to defaults on promote."""
    if not isinstance(payload, dict):
        return None
    try:
        return clamp(TunableConfig.model_validate({**current.model_dump(), **payload}))
    except ValidationError:
        return None


def _tool_payload(response) -> dict | None:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_config":
            return block.input
    return None
