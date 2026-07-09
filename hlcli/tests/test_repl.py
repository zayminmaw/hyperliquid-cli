"""REPL shell: argv injection, meta-commands, completion, header math, dispatch.

The interactive I/O loop is a thin shell over pure functions — those are what we
test here. No network, no keys: everything runs on paper / in-memory fixtures.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console
from typer.main import get_command

from hlcli.cli.app import app
from hlcli.cli import context
from hlcli.cli.context import GlobalState
from hlcli.cli.repl import (
    Session,
    _dispatch,
    _watch_row,
    assemble_argv,
    completions,
    position_rows,
    render_header,
    render_prompt,
    run_line,
)
from hlcli.core.types import Network, Position, Side


@pytest.fixture
def command():
    return get_command(app)


@pytest.fixture
def console():
    # A silent, non-terminal console so run_line's prints don't leak into test output.
    return Console(file=io.StringIO(), force_terminal=False)


def _session(**kw) -> Session:
    return Session(network=kw.pop("network", Network.PAPER), **kw)


# --- assemble_argv: session flags are injected, per-line flags win --------------------

def test_argv_injects_session_network():
    argv = assemble_argv(_session(network=Network.TESTNET), ["markets", "ls"])
    assert argv == ["--network", "testnet", "markets", "ls"]


def test_argv_injects_account_json_dry_yes():
    session = _session(network=Network.TESTNET, account="alice", json=True, dry_run=True, yes=True)
    argv = assemble_argv(session, ["account", "positions"])
    assert argv == [
        "--network", "testnet", "--account", "alice", "--json", "--dry-run", "-y",
        "account", "positions",
    ]


def test_argv_per_line_flag_overrides_session():
    # A per-line --network is not duplicated; the session default is skipped.
    argv = assemble_argv(_session(network=Network.PAPER), ["--network", "testnet", "markets", "ls"])
    assert argv.count("--network") == 1
    assert argv == ["--network", "testnet", "markets", "ls"]


def test_argv_off_flags_not_injected():
    argv = assemble_argv(_session(network=Network.PAPER), ["markets", "ls"])
    assert "--json" not in argv and "--dry-run" not in argv and "-y" not in argv


# --- meta-commands mutate the session -------------------------------------------------

def test_use_switches_network(command, console):
    session = _session()
    assert run_line(session, "use testnet", command=command, console=console) is True
    assert session.network is Network.TESTNET


def test_use_account_sets_and_clears(command, console):
    session = _session(network=Network.TESTNET)
    run_line(session, "use account alice", command=command, console=console)
    assert session.account == "alice"
    run_line(session, "use account -", command=command, console=console)
    assert session.account is None


def test_switching_network_clears_account(command, console):
    session = _session(network=Network.TESTNET, account="alice")
    run_line(session, "use paper", command=command, console=console)
    assert session.network is Network.PAPER
    assert session.account is None


def test_use_rejects_bad_network(command, console):
    session = _session()
    run_line(session, "use moon", command=command, console=console)
    assert session.network is Network.PAPER  # unchanged


def test_set_toggles_flags(command, console):
    session = _session()
    run_line(session, "set json on", command=command, console=console)
    assert session.json is True
    run_line(session, "set header off", command=command, console=console)
    assert session.header is False
    run_line(session, "set dry-run on", command=command, console=console)
    assert session.dry_run is True


def test_set_rejects_unknown_field(command, console):
    session = _session()
    run_line(session, "set bogus on", command=command, console=console)
    assert session.json is False and session.header is True


# --- loop control ---------------------------------------------------------------------

def test_exit_returns_false(command, console):
    assert run_line(_session(), "exit", command=command, console=console) is False
    assert run_line(_session(), "quit", command=command, console=console) is False


def test_blank_line_continues(command, console):
    assert run_line(_session(), "   ", command=command, console=console) is True


def test_unknown_command_stays_in_loop(command, console):
    # A bad CLI command raises a Click UsageError under the hood — the shell catches
    # it, prints, and keeps going (returns True) rather than exiting.
    assert run_line(_session(), "bogus subcmd", command=command, console=console) is True


def test_unbalanced_quotes_reported_not_raised(command, console):
    assert run_line(_session(), 'trade order "oops', command=command, console=console) is True


# --- completion -----------------------------------------------------------------------

def test_completion_top_level_lists_commands_and_meta(command):
    top = completions(command, "")
    assert "markets" in top and "account" in top
    assert "use" in top and "set" in top and "exit" in top


def test_completion_subcommands(command):
    assert set(completions(command, "markets ")) >= {"ls", "prices"}


def test_completion_use_targets(command):
    assert completions(command, "use ") == ["account", "paper", "testnet", "mainnet"]


def test_completion_set_values(command):
    assert completions(command, "set json ") == ["on", "off"]


# --- header / PnL math ----------------------------------------------------------------

def test_position_rows_pnl_percent_long():
    pos = Position(coin="BTC", side=Side.LONG, size=0.15, entry_price=64000.0, unrealized_pnl=63.0)
    (row,) = position_rows([pos], {"BTC": 64420.0})
    assert row.mark == 64420.0
    assert row.upnl == 63.0
    assert row.upnl_pct == pytest.approx(0.66, abs=0.01)  # 63 / (0.15*64000) * 100


def test_position_rows_missing_mark_and_short_sign():
    pos = Position(coin="ETH", side=Side.SHORT, size=2.0, entry_price=3450.0, unrealized_pnl=-58.0)
    (row,) = position_rows([pos], {})  # no mark for ETH
    assert row.mark is None
    assert row.side == "short"
    assert row.upnl_pct < 0


def test_watch_row_formats_like_header_missing_mark():
    pos = Position(coin="ETH", side=Side.SHORT, size=2.0, entry_price=3450.0, unrealized_pnl=-58.0)
    (row,) = position_rows([pos], {})
    cells = _watch_row(row)
    assert cells["mark"] == "-"        # None renders as a placeholder, not the string "None"
    assert cells["side"] == "short"
    assert "-58" in cells["uPnL"]      # formatted + signed, same as the header


# --- prompt rendering -----------------------------------------------------------------

def test_prompt_plain_forms():
    assert render_prompt(_session(), color=False) == "hl(paper)> "
    s = _session(network=Network.TESTNET, account="alice", json=True)
    assert render_prompt(s, color=False) == "hl(testnet:alice)[json]> "
    s2 = _session(network=Network.MAINNET, json=True, dry_run=True)
    assert render_prompt(s2, color=False) == "hl(mainnet)[json,dry]> "


# --- dispatch: exit codes ------------------------------------------------------------

class _FakeCommand:
    """Stands in for the click group: `main` returns an exit code (what click does
    under standalone_mode=False), rather than raising Exit."""

    def __init__(self, code):
        self._code = code

    def main(self, argv, **kwargs):
        return self._code


def test_dispatch_surfaces_nonzero_exit(console):
    _dispatch(_session(), ["anything"], _FakeCommand(2), console)
    assert "exited with code 2" in console.file.getvalue()


def test_dispatch_silent_on_success(console):
    _dispatch(_session(), ["anything"], _FakeCommand(0), console)
    assert "exited" not in console.file.getvalue()


# --- mainnet re-arms the typed confirmation ------------------------------------------

def test_use_mainnet_clears_session_yes(command, console):
    session = _session(yes=True)
    run_line(session, "use mainnet", command=command, console=console)
    assert session.network is Network.MAINNET
    assert session.yes is False  # a carried-over `-y` must not skip the mainnet confirm


def test_argv_per_line_yes_not_duplicated():
    argv = assemble_argv(_session(yes=True), ["--yes", "trade", "cancel-all"])
    assert argv.count("-y") == 0 and argv.count("--yes") == 1


# --- open_env is exception-safe (no leaked state store) ------------------------------

def test_open_env_closes_store_when_exchange_build_fails(monkeypatch):
    closed = []

    class FakeStore:
        def close(self):
            closed.append(True)

    def boom(state, *, for_write):
        raise RuntimeError("no account")

    monkeypatch.setattr(context, "open_state", lambda caps, net: FakeStore())
    monkeypatch.setattr(context, "load_tunable", lambda: object())
    monkeypatch.setattr(context, "build_for", boom)

    state = GlobalState(network=Network.TESTNET, account=None, json_out=False, dry_run=False, yes=False)
    with pytest.raises(RuntimeError, match="no account"):
        context.open_env(state, for_write=False)
    assert closed == [True]  # store closed despite the build failure


# --- header degrades instead of crashing --------------------------------------------

def test_render_header_degrades_when_book_unreachable(monkeypatch, console):
    from hlcli.cli import repl

    def boom(state, *, for_write):
        raise RuntimeError("unreachable")

    monkeypatch.setattr(repl, "open_env", boom)
    render_header(_session(network=Network.TESTNET), console)  # must not raise
    assert "positions unavailable" in console.file.getvalue()
