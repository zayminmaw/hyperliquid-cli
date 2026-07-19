"""Alerting (PLAN.md §7): structured JSONL to a log + stderr, and the runner's
fire / reject / halted hooks."""

import io
import json

from hlcli.core.types import Candidate, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.runner import run_once
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps, tunable

NOW = 1_000_000.0


def _cand(id="a", coin="BTC") -> Candidate:
    return Candidate(id=id, coin=coin, side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW)


def test_alerter_writes_jsonl_and_stream(tmp_path):
    stream = io.StringIO()
    path = tmp_path / "alerts.log"
    alerter = Alerter(path, stream=stream)
    record = alerter.alert("fire", level="info", coin="BTC", size=1.0)

    assert record["event"] == "fire" and record["coin"] == "BTC" and "ts" in record
    assert "fire" in stream.getvalue()
    logged = json.loads(path.read_text().splitlines()[0])
    assert logged["event"] == "fire" and logged["level"] == "info"


def test_alerter_append_accumulates(tmp_path):
    path = tmp_path / "alerts.log"
    a = Alerter(path, stream=None)
    a.alert("fire")
    a.alert("reject", level="warning")
    assert len(path.read_text().splitlines()) == 2


class CapturingAlerter:
    def __init__(self):
        self.events = []

    def alert(self, event, *, level="info", **fields):
        self.events.append({"event": event, "level": level, **fields})


def _run(tmp_path, *, breaker=False, marks=None):
    state = StateStore(tmp_path / "state.db")
    if breaker:
        state.set_breaker(True)
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    state.enqueue(_cand())
    alerter = CapturingAlerter()
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)
    return alerter


def test_fire_emits_one_fire_alert(tmp_path):
    alerter = _run(tmp_path)
    assert [e["event"] for e in alerter.events] == ["fire"]


def test_breaker_emits_halted_and_reject(tmp_path):
    alerter = _run(tmp_path, breaker=True)
    events = {e["event"] for e in alerter.events}
    assert events == {"halted", "reject"}
    halted = next(e for e in alerter.events if e["event"] == "halted")
    assert halted["level"] == "critical" and halted["reason"] == "kill switch"
    reject = next(e for e in alerter.events if e["event"] == "reject")
    assert reject["reason"] == "breaker tripped"


def test_halted_alert_is_edge_triggered_not_per_pass(tmp_path):
    # A persistently-tripped breaker must not spam a "halted" alert every pass.
    state = StateStore(tmp_path / "state.db")
    state.set_breaker(True)
    alerter = CapturingAlerter()
    for i in range(3):
        state.enqueue(_cand(id=f"c{i}"))
        ex = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
        run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)
    assert sum(e["event"] == "halted" for e in alerter.events) == 1  # once, not three times


def test_unmanaged_position_alert_is_edge_triggered(tmp_path):
    # A position on the exchange with no ledger row (crash between fill and write,
    # or a manual trade) alerts once — not every pass.
    from hlcli.core.types import Network, Position
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    stray = [Position(coin="ETH", side=Side.LONG, size=1.0, entry_price=1500.0)]
    alerter = CapturingAlerter()
    for _ in range(2):
        ex = FakeLiveExchange(Network.MAINNET, positions=stray)
        run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)
    unmanaged = [e for e in alerter.events if e["event"] == "unmanaged_position"]
    assert len(unmanaged) == 1 and unmanaged[0]["coins"] == ["ETH"]


def test_liquidation_near_alert_is_edge_triggered(tmp_path):
    # Wave-2 M: a position whose mark (100) sits within the 5% floor of its liquidation
    # price (98 → 2% away) pages a critical alert once, not every pass.
    from hlcli.core.types import Network, Position
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    pos = [Position(coin="BTC", side=Side.LONG, size=1.0, entry_price=105.0, liquidation_px=98.0)]
    alerter = CapturingAlerter()
    for _ in range(2):
        ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 100.0}, positions=pos)
        run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)
    near = [e for e in alerter.events if e["event"] == "liquidation_near"]
    assert len(near) == 1 and near[0]["coins"] == ["BTC"]


def test_no_liquidation_alert_when_far_or_unknown(tmp_path):
    # Far from liquidation (50% away) or no liquidationPx at all (null — verified live) is safe.
    from hlcli.core.types import Network, Position
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    pos = [
        Position(coin="BTC", side=Side.LONG, size=1.0, entry_price=105.0, liquidation_px=50.0),
        Position(coin="ETH", side=Side.LONG, size=1.0, entry_price=1500.0),  # liquidation_px None
    ]
    alerter = CapturingAlerter()
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 100.0, "ETH": 1500.0}, positions=pos)
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, alerter=alerter, now=NOW)
    assert not [e for e in alerter.events if e["event"] == "liquidation_near"]


def test_dry_run_writes_no_breaker_day_state(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
    state.enqueue(_cand())
    run_once(ex, state, caps(), tunable(), decide_fn=act_now, dry_run=True, now=NOW)
    assert state.meta_get("breaker_day") is None  # dry-run mutated nothing
    assert state.meta_get("day_start_equity") is None


def test_no_alerter_is_a_silent_noop(tmp_path):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(), state=state)
    state.enqueue(_cand())
    s = run_once(ex, state, caps(), tunable(), decide_fn=act_now, now=NOW)  # alerter defaults None
    assert s.fired == 1
