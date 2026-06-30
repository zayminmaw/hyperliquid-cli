"""Clamp is the safety contract — no tunable value reaches the order path unbounded."""

import json
from pathlib import Path

import pytest

from hlcli.core.config_schema import (
    ConfigError,
    ConvictionSizing,
    RegimeGate,
    TunableConfig,
    clamp,
    load_tunable,
)


def test_clamp_bounds_every_field():
    raw = TunableConfig(
        risk_per_trade_pct=999.0,
        max_candidates_per_pass=9999,
        decision_temperature=9.0,
        sizing=ConvictionSizing(min_conviction=5.0, floor_fraction=2.0, ceil_fraction=-1.0),
    )
    c = clamp(raw)

    assert c.risk_per_trade_pct == 5.0  # _RISK_PCT_MAX
    assert c.max_candidates_per_pass == 50  # _MAX_CANDIDATES_CEILING
    assert c.decision_temperature == 1.0
    assert 0.0 <= c.sizing.min_conviction <= 1.0
    assert 0.0 <= c.sizing.floor_fraction <= 1.0
    assert 0.0 <= c.sizing.ceil_fraction <= 1.0


def test_clamp_keeps_floor_below_ceil():
    c = clamp(TunableConfig(sizing=ConvictionSizing(floor_fraction=0.9, ceil_fraction=0.1)))
    assert c.sizing.floor_fraction <= c.sizing.ceil_fraction


def test_clamp_is_idempotent():
    once = clamp(TunableConfig(risk_per_trade_pct=999.0))
    assert clamp(once) == once


def test_clamp_filters_unknown_regimes():
    c = clamp(TunableConfig(regime=RegimeGate(allowed_regimes=("trend", "moon", "vibes"))))
    assert c.regime.allowed_regimes == ("trend",)  # garbage dropped, known kept


def test_clamp_empty_regimes_falls_back_to_known_vocabulary():
    c = clamp(TunableConfig(regime=RegimeGate(allowed_regimes=("nonsense",))))
    assert c.regime.allowed_regimes == ("trend", "range")


def test_missing_file_returns_clamped_defaults():
    cfg = load_tunable(Path("/no/such/active_config.json"))
    assert cfg.risk_per_trade_pct == TunableConfig().risk_per_trade_pct


def test_valid_file_loads_and_clamps(tmp_path: Path):
    p = tmp_path / "active.json"
    p.write_text(json.dumps({"risk_per_trade_pct": 1.25, "sizing": {"min_conviction": 0.4}}))
    cfg = load_tunable(p)
    assert cfg.risk_per_trade_pct == 1.25
    assert cfg.sizing.min_conviction == 0.4


def test_malformed_file_is_surfaced(tmp_path: Path):
    p = tmp_path / "active.json"
    p.write_text("{ not json ")
    with pytest.raises(ConfigError):
        load_tunable(p)
