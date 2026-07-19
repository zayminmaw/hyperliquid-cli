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


def test_config_set_clamps_and_persists(isolated_caps):
    r = runner.invoke(app, ["--json", "config", "set", "risk_per_trade_pct", "999"])
    assert r.exit_code == 0
    assert json.loads(r.output)["effective"] == 5.0  # clamped on write
    show = runner.invoke(app, ["--json", "config", "show"])
    assert json.loads(show.output)["risk_per_trade_pct"] == 5.0  # persisted


def test_config_set_rejects_hard_caps(isolated_caps):
    r = runner.invoke(app, ["config", "set", "max_notional_per_trade", "500"])
    assert r.exit_code == 1
    assert "not a tunable field" in r.output  # hard caps live in .env


def test_config_edit_reclamps_on_save(isolated_caps, monkeypatch):
    import hlcli.cli.commands.config as config_cmd

    path = get_caps().config_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{"risk_per_trade_pct": 999}')  # a hand-broken value
    monkeypatch.setattr(config_cmd, "_launch_editor", lambda p: None)  # no-op editor
    r = runner.invoke(app, ["config", "edit"])
    assert r.exit_code == 0
    from hlcli.core.config_schema import load_tunable
    assert load_tunable(path).risk_per_trade_pct == 5.0  # clamped back on save


def test_config_reset_restores_defaults(isolated_caps):
    runner.invoke(app, ["config", "set", "risk_per_trade_pct", "1.0"])
    r = runner.invoke(app, ["--json", "config", "reset"])
    assert r.exit_code == 0 and json.loads(r.output)["removed"] is True
    show = runner.invoke(app, ["--json", "config", "show"])
    assert json.loads(show.output)["risk_per_trade_pct"] == 0.5  # back to the built-in default


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
    assert payload["liveness"] == "never"  # no heartbeat ever written (audit F)
    assert payload["pending_proposals"] == []


def test_agent_watchdog_paper_never_run(isolated_caps):
    # No heartbeat + empty book: nothing to page about, clean exit.
    result = runner.invoke(app, ["--json", "agent", "watchdog"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["liveness"] == "never" and payload["paged"] is False


def test_agent_watchdog_pages_when_stale_with_positions(isolated_caps, tmp_path, monkeypatch):
    # A dead loop (stale heartbeat) holding an open position must page and exit non-zero (audit F).
    import time
    from hlcli.agent.supervisor import LAST_TICK
    from hlcli.core.types import Side
    from hlcli.state.store import open_state
    from hlcli.core.config import get_caps as _get_caps
    from hlcli.core.types import Network

    monkeypatch.setattr("hlcli.exchange.paper.PaperExchange.get_marks",
                        lambda self, *a, **k: {"BTC": 100.0})  # no network in the test
    state = open_state(_get_caps(), Network.PAPER)
    state.meta_set(LAST_TICK, str(time.time() - 100_000))  # long stale
    state.upsert_paper_position("BTC", Side.LONG, 0.1, 100.0)
    state.close()

    result = runner.invoke(app, ["--json", "agent", "watchdog"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["liveness"] == "stale" and payload["paged"] is True


def test_journal_write_show_ls_paper(isolated_caps):
    result = runner.invoke(app, ["--json", "journal", "write", "--no-narrative"])
    assert result.exit_code == 0
    day = json.loads(result.output)["date"]

    shown = runner.invoke(app, ["--json", "journal", "show", day])
    assert shown.exit_code == 0
    assert "_narrative disabled_" in json.loads(shown.output)["content"]

    listed = runner.invoke(app, ["--json", "journal", "ls"])
    assert json.loads(listed.output)["days"] == [day]


def test_exec_shadow_wires_the_reconciliation_alerter(isolated_caps, monkeypatch):
    # O-2: shadow is exactly where unmanaged-position drift must not be silent — the
    # CLI has to hand run_once an alerter or the runner-level check is skipped.
    import hlcli.cli.commands.exec_ as exec_cmd

    seen = {}
    real = exec_cmd.run_once

    def spy(*args, **kw):
        seen.update(kw)
        return real(*args, **kw)

    monkeypatch.setattr(exec_cmd, "run_once", spy)
    result = runner.invoke(app, ["exec", "shadow"])
    assert result.exit_code == 0
    assert seen.get("alerter") is not None


def test_keystore_error_is_a_domain_error():
    # "key is encrypted — set HL_KEYSTORE_PASSPHRASE" is a routine operator condition:
    # it must render as a one-line CLI error, never a traceback.
    from hlcli.accounts.keystore import KeystoreError
    from hlcli.cli.errors import DOMAIN_ERRORS

    assert KeystoreError in DOMAIN_ERRORS
