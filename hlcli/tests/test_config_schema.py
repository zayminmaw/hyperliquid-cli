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
    get_field,
    load_tunable,
    save_tunable,
    set_field,
    tunable_keys,
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


def test_clamp_replaces_non_finite_values_with_defaults():
    # NaN slides through a min/max clamp as the UPPER bound — a NaN risk pct would
    # silently become the 5% maximum. Non-finite values must fall back to defaults.
    nan, inf = float("nan"), float("inf")
    c = clamp(TunableConfig(
        risk_per_trade_pct=nan,
        decision_temperature=inf,
        sizing=ConvictionSizing(min_conviction=nan, floor_fraction=nan, ceil_fraction=nan),
    ))
    d = TunableConfig()
    assert c.risk_per_trade_pct == d.risk_per_trade_pct
    assert c.decision_temperature == d.decision_temperature
    assert c.sizing.min_conviction == d.sizing.min_conviction
    assert c.sizing.floor_fraction == d.sizing.floor_fraction
    assert c.sizing.ceil_fraction == d.sizing.ceil_fraction


def test_nan_in_config_file_loads_as_defaults(tmp_path: Path):
    # json.loads accepts bare NaN — a corrupt/malicious active_config.json must not
    # reach the order path with NaN-widened values.
    p = tmp_path / "active_config.json"
    p.write_text('{"risk_per_trade_pct": NaN}')
    assert load_tunable(p).risk_per_trade_pct == TunableConfig().risk_per_trade_pct


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


def test_set_field_scalar_and_nested_types():
    cfg = TunableConfig()
    assert set_field(cfg, "risk_per_trade_pct", "1.25").risk_per_trade_pct == 1.25
    assert set_field(cfg, "sizing.enabled", "true").sizing.enabled is True
    assert set_field(cfg, "sizing.enabled", "off").sizing.enabled is False
    assert set_field(cfg, "trail.style", "atr").trail.style == "atr"
    assert set_field(cfg, "max_candidates_per_pass", "7").max_candidates_per_pass == 7
    # a comma list fills the tuple-typed allowed_regimes
    assert set_field(cfg, "regime.allowed_regimes", "trend, range").regime.allowed_regimes == ("trend", "range")


def test_set_field_rejects_hard_caps_unknowns_and_submodels():
    cfg = TunableConfig()
    for bad in ("max_notional_per_trade", "max_leverage", "bogus", "trail.bogus", "sizing"):
        with pytest.raises(KeyError):
            set_field(cfg, bad, "1")  # hard caps / typos / a whole submodel are not settable leaves


def test_set_field_bad_bool_is_a_value_error():
    with pytest.raises(ValueError):
        set_field(TunableConfig(), "sizing.enabled", "maybe")


def test_set_field_does_not_mutate_input():
    cfg = TunableConfig()
    set_field(cfg, "risk_per_trade_pct", "3.0")
    assert cfg.risk_per_trade_pct == TunableConfig().risk_per_trade_pct  # returned a copy


def test_save_tunable_clamps_on_write_and_round_trips(tmp_path: Path):
    p = tmp_path / "active_config.json"
    # an out-of-range value written via save_tunable comes back clamped, not raw
    save_tunable(TunableConfig(risk_per_trade_pct=999.0), p)
    assert load_tunable(p).risk_per_trade_pct == 5.0
    save_tunable(set_field(load_tunable(p), "trail.style", "percent"), p)
    assert load_tunable(p).trail.style == "percent"


def test_tunable_keys_lists_nested_leaves():
    keys = tunable_keys()
    assert "risk_per_trade_pct" in keys
    assert "sizing.enabled" in keys
    assert "trail.style" in keys
    assert "agent.exec_interval_minutes" in keys
    assert "sizing" not in keys  # submodels are not leaves


def test_get_field_reads_nested():
    cfg = set_field(TunableConfig(), "sizing.min_conviction", "0.42")
    assert get_field(cfg, "sizing.min_conviction") == 0.42


def test_clamp_bounds_agent_cadences():
    from hlcli.core.config_schema import AgentConfig

    wild = TunableConfig(agent=AgentConfig(
        intake_poll_seconds=0.01, exec_interval_minutes=10_000, sentry_interval_seconds=1))
    a = clamp(wild).agent
    assert a.intake_poll_seconds == 1.0
    assert a.exec_interval_minutes == 120.0
    assert a.sentry_interval_seconds == 10.0

    nan = TunableConfig(agent=AgentConfig(intake_poll_seconds=float("nan")))
    assert clamp(nan).agent.intake_poll_seconds == AgentConfig().intake_poll_seconds
