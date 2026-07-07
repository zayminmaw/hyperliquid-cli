"""CLI smoke tests — Phase 0 gate + Phase 1 surfaces (paper/store only; no live calls)."""

import json

import pytest
from typer.testing import CliRunner

from hlcli.cli.app import app
from hlcli.core.config import get_caps

runner = CliRunner()


@pytest.fixture
def isolated_caps(tmp_path, monkeypatch):
    """Point the account store/keystore at a temp dir, not the dev's home."""
    monkeypatch.setenv("HL_DATA_DIR", str(tmp_path))
    get_caps.cache_clear()
    yield
    get_caps.cache_clear()


def test_help_works():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "exec" in result.output


def test_exec_once_paper_empty_stream(isolated_caps):
    result = runner.invoke(app, ["exec", "once"])
    assert result.exit_code == 0
    assert "seen" in result.output


def test_exec_once_json_is_machine_readable(isolated_caps):
    result = runner.invoke(app, ["--json", "exec", "once"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["network"] == "paper"
    assert payload["seen"] == 0 and payload["fired"] == 0


def test_exec_report_surfaces_graduation(isolated_caps):
    result = runner.invoke(app, ["--json", "exec", "report"])
    assert result.exit_code == 0
    grad = json.loads(result.output)["graduation"]
    assert grad["ready"] is False and grad["n"] == 0  # empty ledger isn't mainnet-ready


def test_config_show_works():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0


def test_unbuilt_verb_is_a_clear_stub():
    result = runner.invoke(app, ["config", "set"])
    assert result.exit_code == 1
    assert "Phase 4" in result.output


def test_tune_run_no_ops_on_empty_record(isolated_caps):
    # No resolved trades → both tuners are sample-gated, no model is called, nothing written.
    result = runner.invoke(app, ["--json", "tune", "run"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["config"] == "no_eligible_cohort"
    assert payload["prompt"] == "insufficient_data"
    assert payload["written"] == []


def test_account_add_and_list(isolated_caps):
    add = runner.invoke(app, ["--network", "testnet", "account", "add", "mon", "--address", "0xabc", "--read-only"])
    assert add.exit_code == 0, add.output
    ls = runner.invoke(app, ["--network", "testnet", "account", "ls"])
    assert ls.exit_code == 0
    assert "mon" in ls.output


def test_trade_dry_run_places_nothing():
    result = runner.invoke(app, ["--dry-run", "trade", "order", "limit", "BTC", "long", "0.001", "50000"])
    assert result.exit_code == 0
    assert "dry" in result.output.lower()


def test_trade_rejects_notional_over_cap():
    result = runner.invoke(app, ["trade", "order", "limit", "BTC", "long", "1", "50000"])
    assert result.exit_code != 0  # notional 50,000 > MAX_NOTIONAL_PER_TRADE


def test_trade_rejects_disallowed_coin():
    result = runner.invoke(app, ["trade", "order", "limit", "DOGE", "long", "1", "1"])
    assert result.exit_code != 0


def test_agent_status_paper(isolated_caps):
    result = runner.invoke(app, ["--json", "agent", "status"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["network"] == "paper"
    assert payload["running"] is False
    assert payload["pending_proposals"] == []
