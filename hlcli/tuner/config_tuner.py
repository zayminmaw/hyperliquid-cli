"""Config tuner (PLAN.md §10) — out of the order path, propose → approve.

Reads resolved-trade cohorts and asks `claude-opus-4-8` to propose edits to the
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
from hlcli.tuner.stats import MIN_COHORT_SAMPLES, Cohort, cohorts, summary

CONFIG_TOOL = {
    "name": "submit_config",
    "description": "Submit the full proposed tunable config. Values are clamped to safe bounds on load.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "risk_per_trade_pct": {"type": "number"},
            "regime": {
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "allowed_regimes": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["enabled", "allowed_regimes"],
                "additionalProperties": False,
            },
            "sizing": {
                "type": "object",
                "properties": {
                    "min_conviction": {"type": "number"},
                    "floor_fraction": {"type": "number"},
                    "ceil_fraction": {"type": "number"},
                },
                "required": ["min_conviction", "floor_fraction", "ceil_fraction"],
                "additionalProperties": False,
            },
            "max_candidates_per_pass": {"type": "integer"},
            "decision_temperature": {"type": "number"},
            "max_hold_minutes": {"type": "integer"},
        },
        "required": [
            "risk_per_trade_pct", "regime", "sizing",
            "max_candidates_per_pass", "decision_temperature", "max_hold_minutes",
        ],
        "additionalProperties": False,
    },
}

SYSTEM_PROMPT = (
    "You tune the strategy config of a crypto-futures executor from its own resolved-trade "
    "record. You are out of the order path: your proposal is reviewed by a human and clamped "
    "to safe bounds before it can ever take effect, so propose the config you believe maximizes "
    "expectancy — do not self-censor toward the current values.\n\n"
    "Favor regimes/conviction-buckets/coins with positive expectancy (avg_r) and adequate sample "
    "size; pull risk and size away from cohorts that lose. Make incremental, defensible changes "
    "grounded in the cohort stats, not large speculative swings. Always answer via submit_config "
    "with the FULL config."
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
        messages=[{"role": "user", "content": _prompt(tunable, eligible, summary(trades))}],
    )

    proposed = _validate(_tool_payload(response))
    if proposed is None:
        return ConfigProposal(None, "invalid_output", eligible)
    return ConfigProposal(proposed, "ok", eligible)


def _prompt(current: TunableConfig, eligible: list[Cohort], overall: dict) -> str:
    return json.dumps({
        "current_config": current.model_dump(),
        "overall": overall,
        "cohorts": [c.__dict__ for c in eligible],
    }, indent=2)


def _validate(payload: object) -> TunableConfig | None:
    if not isinstance(payload, dict):
        return None
    try:
        return clamp(TunableConfig.model_validate(payload))
    except ValidationError:
        return None


def _tool_payload(response) -> dict | None:
    for block in getattr(response, "content", []):
        if getattr(block, "type", None) == "tool_use" and block.name == "submit_config":
            return block.input
    return None
