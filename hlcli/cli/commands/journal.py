"""`hl journal` — the daily trade journal (PLAN.md §15.3).

`write` builds the deterministic digest for a UTC day and appends the one-per-day
LLM reflection (cached in state meta — re-writing a day never re-rolls it).
The agent's daily job calls the same writer for the day that just ended.
"""

from __future__ import annotations

import time
from typing import Optional

import typer

from hlcli.cli.context import open_env, state_of
from hlcli.cli.output import emit, note
from hlcli.core.config import get_caps
from hlcli.journal.digest import utc_date
from hlcli.journal.writer import journal_dir, journal_path, write_journal
from hlcli.safety.alerts import network_alerter
from hlcli.tuner.promote import pending_proposals

app = typer.Typer(no_args_is_help=True, help="Daily trade journal.")


@app.command("write")
def write(
    ctx: typer.Context,
    date: Optional[str] = typer.Option(None, "--date", help="UTC day, YYYY-MM-DD (default: today)"),
    no_narrative: bool = typer.Option(False, "--no-narrative", help="skip the LLM reflection"),
) -> None:
    """Build (or rebuild) the journal for a day. The digest always regenerates;
    the reflection is written once per day and reused."""
    g = state_of(ctx)
    exchange, state, caps, tunable = open_env(g, for_write=False)
    day = date or utc_date(time.time())
    path = write_journal(
        exchange, state, caps, g.network, day,
        narrative=not no_narrative and tunable.agent.journal_narrative,
        alerter=network_alerter(caps, g.network),
        pending_proposals=pending_proposals(caps),
    )
    emit({"network": g.network.value, "date": day, "path": str(path)},
         as_json=g.json_out, title="journal write")


@app.command("show")
def show(
    ctx: typer.Context,
    date: Optional[str] = typer.Argument(None, help="UTC day, YYYY-MM-DD (default: today)"),
) -> None:
    """Print a day's journal."""
    g = state_of(ctx)
    day = date or utc_date(time.time())
    path = journal_path(get_caps(), g.network, day)
    if not path.exists():
        raise typer.BadParameter(f"no journal for {day} on {g.network.value} — run `hl journal write`")
    content = path.read_text()
    if g.json_out:
        emit({"network": g.network.value, "date": day, "content": content}, as_json=True)
    else:
        print(content)


@app.command("ls")
def ls(ctx: typer.Context) -> None:
    """List journaled days for the current network."""
    g = state_of(ctx)
    directory = journal_dir(get_caps(), g.network)
    days = sorted(p.stem for p in directory.glob("*.md")) if directory.exists() else []
    if g.json_out:
        emit({"network": g.network.value, "days": days}, as_json=True)
    else:
        for day in days:
            note(day)
        if not days:
            note("no journals yet — `hl journal write`")
