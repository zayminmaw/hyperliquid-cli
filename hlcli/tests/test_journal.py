"""Journal (PLAN.md §15.3): the digest reconciles with the state store, day slicing
is exact, and the narrative is one-per-day, cached, and failure-proof."""

from __future__ import annotations

import io
import json
from types import SimpleNamespace

from hlcli.core.types import Network, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.journal.digest import build_digest, day_bounds, render, utc_date
from hlcli.journal.narrative import JournalNarrative, narrate
from hlcli.journal.writer import journal_path, write_journal
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore
from hlcli.tests._helpers import caps

DAY = "2026-07-07"
T0, T1 = day_bounds(DAY)
IN_DAY = T0 + 3600
BEFORE = T0 - 3600


def seeded_state(tmp_path) -> StateStore:
    state = StateStore(tmp_path / "state.db")
    # decisions: one gate reject, one pre-gate reject, one drop, one defer — plus yesterday's noise
    state.log_decision("c1", IN_DAY, decision={"action": "act", "conviction": 0.8, "rationale": "clean breakout"},
                       gate={"approved": False, "reason": "rr floor"}, context={"coin": "BTC"})
    state.log_decision("c2", IN_DAY, context={"coin": "ETH", "rejected": "no mark for coin"})
    state.log_decision("c3", IN_DAY, context={"coin": "SOL", "dropped": "schema_invalid"})
    state.log_decision("c4", IN_DAY, decision={"action": "act"}, context={"coin": "BTC", "wait": "deferred"})
    state.log_decision("old", BEFORE, decision={"action": "act"}, gate={"approved": False, "reason": "rr floor"})
    # trades: one opened+resolved today (win), one loss, one shadow open, one resolved yesterday
    win = state.open_trade("c5", "BTC", Side.LONG, 100.0, 95.0, 110.0, 1.0, 0.9, "trend", IN_DAY)
    state.resolve_trade(win, "won", 110.0, 10.0, 2.0, IN_DAY + 100)
    loss = state.open_trade("c6", "ETH", Side.LONG, 100.0, 95.0, 110.0, 1.0, 0.5, None, IN_DAY)
    state.resolve_trade(loss, "lost", 95.0, -5.0, -1.0, IN_DAY + 200)
    state.open_trade("c7", "SOL", Side.LONG, 50.0, 48.0, 55.0, 1.0, 0.7, None, IN_DAY, shadow=True)
    old = state.open_trade("c8", "BTC", Side.LONG, 100.0, 95.0, 110.0, 1.0, 0.9, None, BEFORE - 100)
    state.resolve_trade(old, "won", 110.0, 10.0, 2.0, BEFORE)
    # sentry actions
    state.log_sentry(IN_DAY, win, "BTC", "breakeven")
    state.log_sentry(IN_DAY, win, "BTC", "scale_out")
    state.log_sentry(BEFORE, old, "BTC", "breakeven")
    return state


def test_digest_reconciles_and_slices_by_day(tmp_path):
    state = seeded_state(tmp_path)
    alerts = tmp_path / "alerts.log"
    alerts.write_text(json.dumps(
        {"ts": IN_DAY, "level": "critical", "event": "halted", "reason": "kill switch"}) + "\n")

    d = build_digest(PaperExchange(10_000.0, state=state), state, Network.PAPER, DAY,
                     alerts_path=alerts, pending_proposals=["proposed_config.json"])

    assert (d.fired, d.shadow_fired) == (2, 1)
    assert (d.rejected, d.dropped, d.deferred) == (2, 1, 1)
    assert d.reject_reasons == {"rr floor": 1, "no mark for coin": 1}
    assert d.decided == 2
    assert d.decisions[0] == {"coin": "BTC", "action": "act", "conviction": 0.8, "rationale": "clean breakout"}
    assert "- BTC: act (conviction 0.8) — clean breakout" in render(d)
    assert len(d.resolved) == 2 and (d.wins, d.losses) == (1, 1)
    assert d.realized == 5.0
    assert d.avg_r == 0.5
    assert d.profit_factor == 2.0
    assert d.sentry_actions == {"breakeven": 1, "scale_out": 1}
    assert d.alert_events == {"halted": 1}
    assert d.pending_proposals == ["proposed_config.json"]
    assert d.equity == PaperExchange(10_000.0, state=state).equity()


def test_scaled_partials_are_not_counted_as_opened(tmp_path):
    # One real fire that scales out becomes a parent + a `scaled` child sharing its
    # opened_at; only the entry should count as fired/opened.
    state = StateStore(tmp_path / "s.db")
    parent = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, "trend", IN_DAY)
    state.split_trade(parent, 1.0, 110.0, 10.0, 2.0, IN_DAY + 100)  # bank half → `scaled` child

    d = build_digest(PaperExchange(10_000.0, state=state), state, Network.PAPER, DAY)
    assert d.fired == 1 and len(d.opened) == 1           # the entry only, not the partial
    assert [t["status"] for t in d.resolved] == ["scaled"]  # the partial shows up as an exit


def test_journal_write_today_defers_the_narrative(tmp_path):
    # Journaling a still-open day must not cache a partial-day reflection/lesson that the
    # nightly job would then reuse forever.
    state = seeded_state(tmp_path)
    c = caps(data_dir=tmp_path)
    today = utc_date(IN_DAY)
    calls = []

    def fake_narrate(md, caps_):
        calls.append(md)
        return JournalNarrative(reflection="partial", lesson="partial lesson")

    path = write_journal(PaperExchange(10_000.0, state=state), state, c, Network.PAPER, today,
                         narrate_fn=fake_narrate, now=IN_DAY)
    assert calls == []                                   # the model was never called
    assert "deferred until the day closes" in path.read_text()
    assert state.meta_get("journal_narrative_" + today) is None
    assert state.recent_reflections(1) == []             # no partial-day lesson stored


def test_render_contains_every_section(tmp_path):
    d = build_digest(PaperExchange(10_000.0), StateStore(tmp_path / "s.db"), Network.PAPER, DAY)
    text = render(d)
    for heading in ("# Trade journal — paper — 2026-07-07", "## Day at a glance",
                    "## Executor", "## Trades", "## Sentry", "## Operational alerts"):
        assert heading in text
    assert "no trades today" in text


def test_write_is_idempotent_and_narrative_is_once_per_day(tmp_path):
    state = seeded_state(tmp_path)
    c = caps(data_dir=tmp_path)
    calls = []

    def fake_narrate(digest_md, caps_):
        calls.append(digest_md)
        return JournalNarrative(reflection="well-skipped chop; keep honoring the R:R floor",
                                lesson="skip chop")

    for _ in range(2):
        path = write_journal(PaperExchange(10_000.0, state=state), state, c, Network.PAPER, DAY,
                             narrate_fn=fake_narrate)

    assert path == journal_path(c, Network.PAPER, DAY)
    assert len(calls) == 1  # second write reused the cached reflection
    text = path.read_text()
    assert "## Reflection" in text and "well-skipped chop" in text


def test_narrative_failure_degrades_and_alerts(tmp_path):
    state = StateStore(tmp_path / "s.db")
    stream = io.StringIO()

    def boom(digest_md, caps_):
        raise RuntimeError("no api key")

    path = write_journal(PaperExchange(10_000.0), state, caps(data_dir=tmp_path),
                         Network.PAPER, DAY, narrate_fn=boom, alerter=Alerter(stream=stream))

    assert "_narrative unavailable: no api key_" in path.read_text()
    assert "journal_narrative_failed" in stream.getvalue()
    # a later retry may still succeed — nothing was cached
    assert state.meta_get("journal_narrative_" + DAY) is None


def test_narrative_disabled_skips_the_model(tmp_path):
    def boom(digest_md, caps_):
        raise AssertionError("model must not be called")

    path = write_journal(PaperExchange(10_000.0), StateStore(tmp_path / "s.db"),
                         caps(data_dir=tmp_path), Network.PAPER, DAY,
                         narrative=False, narrate_fn=boom)
    assert "_narrative disabled_" in path.read_text()


def test_narrate_calls_the_journal_model(tmp_path):
    captured = {}

    class FakeClient:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            captured.update(kwargs)
            block = SimpleNamespace(type="tool_use", name="submit_journal",
                                    input={"reflection": "  reflect  ", "lesson": " one lesson "})
            return SimpleNamespace(content=[block])

    c = caps(journal_model="claude-opus-4-8", journal_max_tokens=512)
    result = narrate("# digest", c, client=FakeClient())

    assert result == JournalNarrative(reflection="reflect", lesson="one lesson")
    assert captured["model"] == "claude-opus-4-8"
    assert captured["max_tokens"] == 512
    assert captured["tool_choice"] == {"type": "tool", "name": "submit_journal"}
    assert captured["messages"][0]["content"] == "<day_digest>\n# digest\n</day_digest>"


def test_narrate_without_the_tool_call_is_dropped():
    class NoTool:
        def __init__(self):
            self.messages = self

        def create(self, **kwargs):
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="prose only")])

    assert narrate("# digest", caps(), client=NoTool()) is None


def test_day_bounds_and_utc_date_roundtrip():
    t0, t1 = day_bounds(DAY)
    assert t1 - t0 == 86_400
    assert utc_date(t0) == DAY and utc_date(t1 - 1) == DAY and utc_date(t1) == "2026-07-08"


def test_unreadable_alert_lines_are_skipped(tmp_path):
    alerts = tmp_path / "alerts.log"
    alerts.write_text("not json\n" + json.dumps({"ts": IN_DAY, "level": "warning", "event": "reject"}) + "\n")
    d = build_digest(PaperExchange(10_000.0), StateStore(tmp_path / "s.db"), Network.PAPER, DAY,
                     alerts_path=alerts)
    assert d.alert_events == {"reject": 1}
