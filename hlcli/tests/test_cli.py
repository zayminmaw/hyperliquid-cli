"""CLI smoke tests — the Phase 0 gate: `hl --help` and paper `exec once`."""

import json

from typer.testing import CliRunner

from hlcli.cli.app import app

runner = CliRunner()


def test_help_works():
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "exec" in result.output


def test_exec_once_paper_noops():
    result = runner.invoke(app, ["exec", "once"])
    assert result.exit_code == 0
    assert "no-op" in result.output


def test_exec_once_json_is_machine_readable():
    result = runner.invoke(app, ["--json", "exec", "once"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["network"] == "paper"
    assert payload["fired"] == 0


def test_config_show_works():
    result = runner.invoke(app, ["config", "show"])
    assert result.exit_code == 0


def test_unbuilt_verb_is_a_clear_stub():
    result = runner.invoke(app, ["account", "ls"])
    assert result.exit_code == 1
    assert "Phase 1" in result.output
