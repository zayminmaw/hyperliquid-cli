"""Key-handling review (PLAN.md §7, §8): the agent key must never reach the decision
context or the decision log. The keystore-perms check lives in test_accounts."""

import json
import re

from hlcli.core.types import Candidate, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.enrich import EnrichedContext, enrich
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps, tunable

NOW = 1_000_000.0
_KEYISH = re.compile(r"key|secret|private|wallet|mnemonic|seed", re.IGNORECASE)


def test_enriched_context_has_no_key_fields():
    assert not [f for f in EnrichedContext.model_fields if _KEYISH.search(f)]


def test_enrich_output_serializes_without_key_material():
    ctx = enrich(
        Candidate(id="a", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW),
        marks={"BTC": 100.0}, equity=10_000.0, positions=[], realized=0.0, recent=[], tunable=tunable(),
    )
    assert not _KEYISH.search(ctx.model_dump_json())


def test_decision_log_context_stays_within_a_keyless_allowlist(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks({"BTC": 100.0}), state=state)
    state.enqueue(Candidate(id="a", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW))
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)

    rows = state.recent_decisions(limit=10)
    assert rows
    for row in rows:
        context = json.loads(row["context"])
        assert set(context) <= {"equity", "open_coins", "regime"}
