"""`hl repl` — an interactive shell over the existing command surface.

The REPL owns no command logic. It grabs the assembled Click group once
(`typer.main.get_command(app)`) and dispatches each input line through it, so
network resolution, the mainnet gate, and `GlobalState` construction stay in
their single home (the root `@app.callback`) — the shell never reimplements or
bypasses them.

Session context (network / account / json / dry-run / yes) is held here and
injected as global flags in front of each line, so `use testnet` / `set json on`
persist across commands. A per-line flag always overrides the session default.

Above each prompt a positions + live-PnL header is rendered (fresh marks every
time control returns to the prompt); `watch` opens a full-screen ticking table.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import NamedTuple

from rich import box
from rich.console import Console
from rich.table import Table

# Typer 0.26 vendors click as `typer._click`; there is no standalone `click` here.
from typer._click.exceptions import Abort, ClickException

from hlcli.cli.context import GlobalState, open_env
from hlcli.cli.errors import DOMAIN_ERRORS, render_domain_error, render_error
from hlcli.core.config import get_caps
from hlcli.core.network import resolve_network
from hlcli.core.types import Network

# --- session --------------------------------------------------------------------------

# network → (rich style for the header, raw ANSI code for the readline prompt)
_NET_STYLE: dict[Network, tuple[str, str]] = {
    Network.PAPER: ("green", "32"),
    Network.TESTNET: ("yellow", "33"),
    Network.MAINNET: ("bold red", "1;31"),
}
_SET_FIELDS = {"json": "json", "dry-run": "dry_run", "yes": "yes", "header": "header"}
_META = ("use", "set", "show", "status", "watch", "clear", "help", "?", "exit", "quit")


@dataclass
class Session:
    """Persistent REPL context — mirrors the global flags on `GlobalState`."""

    network: Network
    account: str | None = None
    json: bool = False
    dry_run: bool = False
    yes: bool = False
    header: bool = True

    def global_state(self) -> GlobalState:
        return GlobalState(
            network=self.network, account=self.account,
            json_out=self.json, dry_run=self.dry_run, yes=self.yes,
        )


def assemble_argv(session: Session, tokens: list[str]) -> list[str]:
    """Prepend the session's global flags to a command line, skipping any the
    line already carries (so a per-line override wins)."""

    def present(*names: str) -> bool:
        return any(t in names for t in tokens)

    argv: list[str] = []
    if not present("--network"):
        argv += ["--network", session.network.value]
    if session.account and not present("--account"):
        argv += ["--account", session.account]
    if session.json and not present("--json"):
        argv += ["--json"]
    if session.dry_run and not present("--dry-run"):
        argv += ["--dry-run"]
    if session.yes and not present("-y", "--yes"):
        argv += ["-y"]
    return argv + tokens


# --- live positions header ------------------------------------------------------------

def _num(x: float | None) -> str:
    if x is None:
        return "-"
    return f"{x:,.2f}" if abs(x) >= 100 else f"{x:,.4f}"


def _pnl(x: float) -> str:
    return f"[{'green' if x >= 0 else 'red'}]{x:+,.2f}[/]"


def _pct(x: float) -> str:
    return f"[{'green' if x >= 0 else 'red'}]{x:+.2f}%[/]"


class PositionRow(NamedTuple):
    """One position for the header/`watch` tables — pure and typed, so it's unit-tested."""

    coin: str
    side: str
    size: float
    entry: float
    mark: float | None
    upnl: float
    upnl_pct: float


def position_rows(positions, marks: dict[str, float]) -> list[PositionRow]:
    """Raw (uncoloured) rows — coin/side/size/entry/mark/uPnL/uPnL%. Shared by the
    prompt header and the `watch` view; kept pure for testing."""
    rows = []
    for p in positions:
        notional = abs(p.size * p.entry_price)
        pct = (p.unrealized_pnl / notional * 100) if notional else 0.0
        rows.append(PositionRow(
            coin=p.coin, side=p.side.value, size=p.size, entry=p.entry_price,
            mark=marks.get(p.coin), upnl=round(p.unrealized_pnl, 4), upnl_pct=round(pct, 2),
        ))
    return rows


def _positions_table(rows: list[PositionRow]) -> Table:
    table = Table(title="active positions", box=box.SIMPLE, title_style="bold cyan")
    table.add_column("coin")
    table.add_column("side")
    for col in ("size", "entry", "mark", "uPnL", "uPnL%"):
        table.add_column(col, justify="right")
    for r in rows:
        table.add_row(r.coin, r.side, _num(r.size), _num(r.entry),
                      _num(r.mark), _pnl(r.upnl), _pct(r.upnl_pct))
    return table


def _watch_row(r: PositionRow) -> dict:
    """A PositionRow formatted for the `watch` table — same number/colour treatment as
    the prompt header (watch_rows derives its columns from these keys)."""
    return {
        "coin": r.coin, "side": r.side,
        "size": _num(r.size), "entry": _num(r.entry), "mark": _num(r.mark),
        "uPnL": _pnl(r.upnl), "uPnL%": _pct(r.upnl_pct),
    }


def render_header(session: Session, console: Console) -> None:
    """Render the positions + live-PnL header above the next prompt.

    Decorative and best-effort: any failure to reach the book (e.g. a live
    network with no account resolved yet) degrades to a dim one-liner and must
    never break the loop — hence the deliberate broad catch at this UI boundary.
    """
    if not session.header or session.json:
        return
    try:
        exchange, store, _caps, _tunable = open_env(session.global_state(), for_write=False)
        try:
            positions = exchange.get_positions()
            marks = exchange.get_marks()
            equity = exchange.equity()
        finally:
            store.close()
    except Exception as exc:  # UI boundary: degrade, never crash the shell
        console.print(f"[dim]positions unavailable: {exc}[/dim]")
        return

    if not positions:
        console.print("[dim]— no open positions —[/dim]")
        return
    rows = position_rows(positions, marks)
    console.print(_positions_table(rows))
    total = round(sum(r.upnl for r in rows), 4)
    console.print(f"[dim]equity[/] {_num(equity)}   [dim]open[/] {len(rows)}   [dim]uPnL[/] {_pnl(total)}")


def _watch(session: Session, console: Console) -> None:
    """Full-screen live PnL table (rich.Live) until ctrl-c, then back to the prompt."""
    from hlcli.cli.watch import watch_rows

    try:
        exchange, store, _caps, _tunable = open_env(session.global_state(), for_write=False)
    except Exception as exc:  # UI boundary: report and return to the prompt
        render_error(str(exc), console)
        return

    def rows() -> list[dict]:
        return [_watch_row(r) for r in position_rows(exchange.get_positions(), exchange.get_marks())]

    try:
        watch_rows(rows, title="positions")
    finally:
        store.close()


# --- meta commands --------------------------------------------------------------------

def _meta_use(session: Session, tokens: list[str], console: Console) -> None:
    if len(tokens) < 2:
        console.print("usage: use <paper|testnet|mainnet> | use account <alias|->")
        return
    if tokens[1].lower() == "account":
        if len(tokens) < 3:
            console.print("usage: use account <alias|->")
            return
        alias = tokens[2]
        session.account = None if alias == "-" else alias
        console.print(f"account → {session.account or '(default/none)'}")
        return
    try:
        resolved = resolve_network(tokens[1].lower(), get_caps())
    except ValueError as exc:
        render_error(str(exc), console)
        return
    prev = session.account
    session.network = resolved
    session.account = None  # accounts are network-scoped — a stale alias would just fail to resolve
    cleared = f" [dim](account '{prev}' cleared)[/]" if prev else ""
    console.print(f"network → {session.network.value}{cleared}")
    _guard_mainnet_yes(session, console)


def _guard_mainnet_yes(session: Session, console: Console) -> None:
    """Entering mainnet re-arms the typed confirmation: a session-wide `yes` carried
    over from paper/testnet must not silently skip the last human check on real money.
    Turn it back on deliberately with `set yes on` while on mainnet."""
    if session.network is Network.MAINNET and session.yes:
        session.yes = False
        console.print("[yellow]mainnet:[/] confirmation re-enabled [dim](`set yes on` to skip again)[/]")


def _meta_set(session: Session, tokens: list[str], console: Console) -> None:
    if len(tokens) != 3 or tokens[2].lower() not in ("on", "off"):
        console.print("usage: set <json|dry-run|yes|header> <on|off>")
        return
    attr = _SET_FIELDS.get(tokens[1].lower())
    if attr is None:
        console.print(f"[red]unknown setting:[/red] {tokens[1]} (json|dry-run|yes|header)")
        return
    setattr(session, attr, tokens[2].lower() == "on")
    console.print(f"{tokens[1].lower()} → {tokens[2].lower()}")


def _print_status(session: Session, console: Console) -> None:
    for label, value in (
        ("network", session.network.value), ("account", session.account or "(default/none)"),
        ("json", session.json), ("dry-run", session.dry_run),
        ("yes", session.yes), ("header", session.header),
    ):
        shown = value if isinstance(value, str) else ("on" if value else "off")
        console.print(f"[cyan]{label:<9}[/]{shown}")


_HELP = """[bold]hl repl[/bold] — interactive shell

[cyan]session[/cyan]
  use <paper|testnet|mainnet>            switch network (clears the account)
  use account <alias|->                  set / clear the account
  set <json|dry-run|yes|header> <on|off> toggle a session flag
  show                                   current session settings

[cyan]live pnl[/cyan]
  the positions header prints above each prompt while `header` is on
  watch                                  full-screen ticking PnL (ctrl-c to stop)

[cyan]commands[/cyan]
  run any hl command without the leading `hl`, e.g. `markets ls`, `account positions`
  <group> --help                         help for a command group

[cyan]exit[/cyan]  exit | quit | ctrl-d
[dim]long-running commands (exec run, agent run, sentry run, -w watches) block until ctrl-c.[/]
"""


# --- dispatch + loop ------------------------------------------------------------------

def _dispatch(session: Session, tokens: list[str], command, console: Console) -> None:
    argv = assemble_argv(session, tokens)
    try:
        # standalone_mode=False makes click RETURN the exit code rather than raise Exit,
        # and re-raise ClickException/Abort for us to render.
        code = command.main(argv, prog_name="hl", standalone_mode=False)
    except DOMAIN_ERRORS as exc:
        render_domain_error(exc, console)
    except ClickException as exc:
        render_error(exc.format_message(), console)
    except Abort:
        console.print("[dim]aborted.[/dim]")
    except SystemExit as exc:  # a command that calls sys.exit() directly must not kill the shell
        if exc.code not in (0, None):
            console.print(f"[red]exited with code {exc.code}[/red]")
    else:
        if code:
            console.print(f"[red]exited with code {code}[/red]")


def run_line(session: Session, line: str, *, command, console: Console) -> bool:
    """Process one input line. Returns False to exit the REPL, True to continue."""
    line = line.strip()
    if not line:
        return True
    try:
        tokens = shlex.split(line)
    except ValueError as exc:
        console.print(f"[red]parse error:[/red] {exc}")
        return True

    keyword = tokens[0].lower()
    if keyword in ("exit", "quit"):
        return False
    if keyword in ("help", "?"):
        console.print(_HELP)
    elif keyword in ("show", "status"):
        _print_status(session, console)
    elif keyword == "clear":
        console.clear()
    elif keyword == "use":
        _meta_use(session, tokens, console)
    elif keyword == "set":
        _meta_set(session, tokens, console)
    elif keyword == "watch":
        _watch(session, console)
    else:
        _dispatch(session, tokens, command, console)
    return True


def render_prompt(session: Session, *, color: bool) -> str:
    """The input prompt, e.g. `hl(testnet:alice)[json]> `. `color=False` for tests."""
    ctx = session.network.value
    if session.account:
        ctx = f"{ctx}:{session.account}"
    flags = [f for f, on in (("json", session.json), ("dry", session.dry_run)) if on]
    tag = f"[{','.join(flags)}]" if flags else ""
    if color:
        _style, ansi = _NET_STYLE[session.network]
        ctx = _rl_colour(ctx, ansi)
    return f"hl({ctx}){tag}> "


# --- readline: history + completion ---------------------------------------------------

# readline needs non-printing bytes wrapped so it computes the prompt width correctly.
_RL_START, _RL_END = "\001", "\002"


def _rl_colour(text: str, ansi: str) -> str:
    return f"{_RL_START}\033[{ansi}m{_RL_END}{text}{_RL_START}\033[0m{_RL_END}"


def _option_names(node) -> list[str]:
    return [opt for p in getattr(node, "params", []) for opt in getattr(p, "opts", []) if opt.startswith("--")]


def completions(command, buffer: str) -> list[str]:
    """Tab-completion candidates for the current input buffer — meta words and the
    walked command tree (nouns → verbs → --options). Pure, so it is unit-tested."""
    parts = buffer.split()
    completing_new = buffer == "" or buffer.endswith(" ")
    head = parts if completing_new else parts[:-1]
    if not head:
        return sorted([*command.commands, *_META])

    first = head[0]
    if first == "use":
        return ["account", "paper", "testnet", "mainnet"]
    if first == "set":
        return sorted(_SET_FIELDS) if len(head) == 1 else ["on", "off"]
    if first in _META:
        return []

    node = command
    for tok in head:
        commands = getattr(node, "commands", {})
        if tok not in commands:
            break
        node = commands[tok]
    return sorted(getattr(node, "commands", {})) + _option_names(node)


def _setup_readline(command):
    try:
        import readline
    except ImportError:
        return None  # readline unavailable — the REPL still works, just no history/completion

    histfile = get_caps().data_dir / "repl_history"
    histfile.parent.mkdir(parents=True, exist_ok=True)
    try:
        readline.read_history_file(histfile)
    except OSError:
        pass
    readline.set_history_length(1000)

    cache: dict = {"key": None, "matches": []}

    def completer(text: str, state: int):
        key = readline.get_line_buffer()
        if cache["key"] != key:
            cache["key"] = key
            cache["matches"] = [c for c in completions(command, key) if c.startswith(text)]
        matches = cache["matches"]
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    readline.set_completer_delims(" ")
    # libedit (the macOS default) and GNU readline take different bind syntax.
    if "libedit" in (getattr(readline, "__doc__", "") or ""):
        readline.parse_and_bind("bind ^I rl_complete")
    else:
        readline.parse_and_bind("tab: complete")
    return histfile


def _save_history(histfile) -> None:
    if histfile is None:
        return
    try:
        import readline

        readline.write_history_file(histfile)
    except OSError:
        pass


def run_repl(state: GlobalState) -> None:
    """Entry point for `hl repl`. Seeds the session from the launch flags."""
    from typer.main import get_command

    from hlcli.cli.app import app  # lazy: app imports this module to register the command

    command = get_command(app)
    session = Session(
        network=state.network, account=state.account,
        json=state.json_out, dry_run=state.dry_run, yes=state.yes,
    )
    console = Console()
    histfile = _setup_readline(command)

    console.print("[bold]hl repl[/] — type [cyan]help[/] for commands, [cyan]exit[/] to quit.")
    _guard_mainnet_yes(session, console)  # a launch-time `-y` doesn't silently persist on mainnet
    try:
        while True:
            render_header(session, console)
            try:
                line = input(render_prompt(session, color=True))
            except EOFError:  # ctrl-d
                console.print()
                break
            except KeyboardInterrupt:  # ctrl-c at the prompt: clear the line, stay in
                console.print("[dim](^C — type `exit` to quit)[/]")
                continue
            try:
                if not run_line(session, line, command=command, console=console):
                    break
            except Exception as exc:  # a command bug drops to the prompt, not out of the shell
                console.print(f"[red]unexpected error:[/red] {type(exc).__name__}: {exc}")
    finally:
        _save_history(histfile)
    console.print("bye.")
