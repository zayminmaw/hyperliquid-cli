"""Reflection memory (PLAN.md §15.4) + the agent daily job (§15.5): lessons are
stored once per day, injected bounded into both LLM contexts, recorded in the
decision log — and tuner proposals auto-promote on paper only."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from hlcli.core.config_schema import AgentConfig, TunableConfig, clamp
from hlcli.core.types import Candidate, Network, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.runner import run_once
from hlcli.agent.daily import run_daily
from hlcli.journal.lessons import recent_lessons
from hlcli.journal.narrative import narrate
from hlcli.journal.writer import write_journal
from hlcli.safety.alerts import Alerter
from hlcli.sentry.context import build_context
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps, tunable

NOW = 1_783_500_000.0


class FakeJournalTool:
    """Forces a `submit_journal` tool call — stands in for the opus narrative."""

    def __init__(self, reflection="quiet day", lesson="honor the R:R floor in chop"):
        self._payload = {"reflection": reflection, "lesson": lesson}
        self.messages = self

    def create(self, **kwargs):
        block = SimpleNamespace(type="tool_use", name="submit_journal", input=self._payload)
        return SimpleNamespace(content=[block])


def test_reflections_upsert_and_order(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.add_reflection("2026-07-05", 1.0, "first")
    state.add_reflection("2026-07-06", 2.0, "second")
    state.add_reflection("2026-07-05", 3.0, "first, revised")

    rows = state.recent_reflections(5)
    assert [r["date"] for r in rows] == ["2026-07-06", "2026-07-05"]
    assert rows[1]["lesson"] == "first, revised"


def test_lessons_are_bounded_by_the_hard_caps(tmp_path):
    state = StateStore(tmp_path / "s.db")
    for i in range(5):
        state.add_reflection(f"2026-07-0{i + 1}", float(i), "L" * 500)

    out = recent_lessons(state, caps(agent_reflect_inject_max=2, agent_reflect_max_chars=100), tunable())
    assert len(out) == 2
    assert all(len(le["lesson"]) == 100 for le in out)


def test_lessons_inject_switches_off(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.add_reflection("2026-07-06", 1.0, "lesson")
    off = clamp(TunableConfig(agent=AgentConfig(reflection_inject=False)))
    assert recent_lessons(state, caps(), off) == []


def test_journal_write_stores_the_distilled_lesson(tmp_path):
    state = StateStore(tmp_path / "s.db")
    write_journal(PaperExchange(10_000.0, marks=FakeMarks()), state, caps(data_dir=tmp_path),
                  Network.PAPER, "2026-07-06",
                  narrate_fn=lambda md, c: narrate(md, c, client=FakeJournalTool()))
    rows = state.recent_reflections(5)
    assert rows[0] == {"date": "2026-07-06", "ts": rows[0]["ts"], "lesson": "honor the R:R floor in chop"}


def test_pass_injects_lessons_and_logs_their_dates(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.add_reflection("2026-07-06", 1.0, "do not chase entries the mark has run past")
    ex = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
    state.enqueue(Candidate(id="c1", coin="BTC", side=Side.LONG, entry=100.0, tp=120.0,
                            sl=90.0, created_at=NOW))
    seen = {}

    def capture(ctx, caps_, tunable_):
        seen["lessons"] = ctx.recent_lessons
        return act_now(ctx, caps_, tunable_)

    s = run_once(ex, state, caps(), tunable(), decide_fn=capture, now=NOW)

    assert s.fired == 1
    assert seen["lessons"] == [{"date": "2026-07-06",
                                "lesson": "do not chase entries the mark has run past"}]
    logged = json.loads(state.decision_for("c1")["context"])
    assert logged["lessons"] == ["2026-07-06"]


def test_management_context_carries_lessons(tmp_path):
    state = StateStore(tmp_path / "s.db")
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 95.0, 120.0, 1.0, 0.8, None, NOW)
    trade = state.open_trades()[0]
    lessons = [{"date": "2026-07-06", "lesson": "protect winners past +2R"}]

    ctx = build_context(trade, mark=105.0, state=state, tunable=tunable(), now=NOW, lessons=lessons)
    assert ctx.recent_lessons == lessons
    assert tid == trade["id"]


def test_run_daily_paper_auto_promotes_pending_proposals(tmp_path):
    state = StateStore(tmp_path / "s.db")
    c = caps(data_dir=tmp_path, config_path=tmp_path / "config" / "active_config.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "proposed_config.json").write_text(
        json.dumps(TunableConfig().model_dump()))
    stream = io.StringIO()

    report = run_daily(PaperExchange(10_000.0, marks=FakeMarks(), state=state), state, c,
                       Network.PAPER, Alerter(stream=stream),
                       client=FakeJournalTool(), tunable=tunable())

    assert report["tuner"]["promoted"] == ["config"]
    assert report["pending_proposals"] == []  # consumed by promotion
    assert (tmp_path / "config" / "active_config.json").exists()
    assert "agent_daily_report" in stream.getvalue()
    # journal (yesterday) landed and the lesson was distilled
    assert report["journal"].endswith(".md")
    assert state.recent_reflections(1)[0]["lesson"] == "honor the R:R floor in chop"


def test_run_daily_testnet_leaves_proposals_pending(tmp_path):
    state = StateStore(tmp_path / "s.db")
    c = caps(data_dir=tmp_path, config_path=tmp_path / "config" / "active_config.json")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "proposed_config.json").write_text(
        json.dumps(TunableConfig().model_dump()))

    report = run_daily(PaperExchange(10_000.0, marks=FakeMarks(), state=state), state, c,
                       Network.TESTNET, Alerter(stream=None),
                       client=FakeJournalTool(), tunable=tunable())

    assert report["tuner"]["promoted"] == []
    assert report["pending_proposals"] == ["proposed_config.json"]  # §15.5: human approves
