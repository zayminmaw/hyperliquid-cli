"""Assembles and persists the journal file: deterministic digest first, then the
reflection section. The narrative for a date is cached in state meta, so re-running
`journal write` rebuilds the digest without paying for (or re-rolling) the LLM call
— one reflection per day, ever, unless the meta row is deleted.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import time

from hlcli.core.config import Caps
from hlcli.core.types import Network
from hlcli.exchange.base import Exchange
from hlcli.journal.digest import build_digest, render
from hlcli.journal.narrative import JournalNarrative, narrate
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore

_META_PREFIX = "journal_narrative_"


def journal_path(caps: Caps, network: Network, date: str) -> Path:
    return caps.data_dir / "journal" / network.value / f"{date}.md"


def write_journal(
    exchange: Exchange,
    state: StateStore,
    caps: Caps,
    network: Network,
    date: str,
    *,
    narrative: bool = True,
    narrate_fn: Callable[[str, Caps], JournalNarrative | None] = narrate,
    alerter: Alerter | None = None,
    pending_proposals: list[str] | None = None,
) -> Path:
    digest_md = render(build_digest(
        exchange, state, network, date,
        alerts_path=caps.data_dir / f"alerts-{network.value}.log",
        pending_proposals=pending_proposals,
    ))
    reflection = _reflection(state, caps, date, digest_md, narrative, narrate_fn, alerter)
    path = journal_path(caps, network, date)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{digest_md}\n## Reflection\n\n{reflection}\n")
    return path


def _reflection(
    state: StateStore,
    caps: Caps,
    date: str,
    digest_md: str,
    narrative: bool,
    narrate_fn: Callable[[str, Caps], JournalNarrative | None],
    alerter: Alerter | None,
) -> str:
    if not narrative:
        return "_narrative disabled_"
    cached = state.meta_get(_META_PREFIX + date)
    if cached:
        return cached  # the lesson was stored alongside it on the fresh call
    try:
        result = narrate_fn(digest_md, caps)
    except Exception as exc:
        # The reflection is an out-of-path nicety: a missing key or API fault must
        # degrade the journal, never fail the daily job that writes it.
        if alerter is not None:
            alerter.alert("journal_narrative_failed", level="warning", date=date, error=str(exc))
        return f"_narrative unavailable: {exc}_"
    if result is None:
        return "_narrative empty_"
    state.meta_set(_META_PREFIX + date, result.reflection)
    if result.lesson:
        state.add_reflection(date, time.time(), result.lesson)  # the §15.4 memory row
    return result.reflection
