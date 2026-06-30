"""Trade resolution: SL/TP/expiry close-outs write correct outcomes to the ledger,
and the paper book realizes the matching P&L. End-to-end open→resolve via run_once."""

from hlcli.core.config_schema import TunableConfig, clamp
from hlcli.core.types import Candidate, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.resolve import resolve_open_trades
from hlcli.executor.runner import run_once
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, act_now, caps

NOW = 1_000_000.0


def _state_ex(tmp_path, marks):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    return state, ex


def _open(state, *, coin="BTC", side=Side.LONG, entry=100, sl=90, tp=120, size=1.0, conv=0.8):
    return state.open_trade("c1", coin, side, entry, sl, tp, size, conv, None, NOW)


def test_long_take_profit_records_win(tmp_path):
    state, ex = _state_ex(tmp_path, {"BTC": 100.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    _open(state)
    n = resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW, marks={"BTC": 125.0})
    assert n == 1
    t = state.resolved_trades()[0]
    assert t["status"] == "won" and t["exit_price"] == 120 and t["r_multiple"] == 2.0
    assert t["realized"] == 20.0           # (120-100) * 1.0
    assert ex.get_positions() == []        # paper book closed


def test_long_stop_loss_records_loss(tmp_path):
    state, ex = _state_ex(tmp_path, {"BTC": 100.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    _open(state)
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW, marks={"BTC": 85.0})
    t = state.resolved_trades()[0]
    assert t["status"] == "lost" and t["exit_price"] == 90 and t["r_multiple"] == -1.0


def test_short_take_profit(tmp_path):
    state, ex = _state_ex(tmp_path, {"ETH": 1500.0})
    state.upsert_paper_position("ETH", Side.SHORT, 2.0, 1500.0)
    _open(state, coin="ETH", side=Side.SHORT, entry=1500, sl=1600, tp=1400, size=2.0)
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW, marks={"ETH": 1390.0})
    t = state.resolved_trades()[0]
    assert t["status"] == "won" and t["realized"] == 200.0   # (1500-1400) * 2.0


def test_untriggered_trade_stays_open(tmp_path):
    state, ex = _state_ex(tmp_path, {"BTC": 100.0})
    _open(state)
    assert resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW, marks={"BTC": 105.0}) == 0
    assert len(state.open_trades()) == 1


def test_expiry_closes_at_mark(tmp_path):
    state, ex = _state_ex(tmp_path, {"BTC": 100.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    _open(state)
    cfg = clamp(TunableConfig(max_hold_minutes=60))
    later = NOW + 61 * 60                       # 61 minutes later, price between SL and TP
    resolve_open_trades(ex, state, caps(), cfg, later, marks={"BTC": 108.0})
    t = state.resolved_trades()[0]
    assert t["status"] == "expired" and t["exit_price"] == 108.0 and t["realized"] == 8.0


def test_native_protected_books_actual_close_fill_not_the_level(tmp_path):
    # On a live backend the resolver's market close fills with slippage; the ledger
    # must record that fill (here 88, not the 90 stop) so expectancy stays honest.
    from hlcli.core.types import Network, OrderType
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 85.0}, fill_price=88.0)
    _open(state)  # long, entry 100, sl 90
    n = resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                            marks={"BTC": 85.0}, native_protected=True)
    assert n == 1
    t = state.resolved_trades()[0]
    assert t["status"] == "lost" and t["exit_price"] == 88.0
    assert t["realized"] == -12.0  # (88 - 100) * 1.0, not the idealized (90-100)
    assert ex.placed[0].order_type is OrderType.MARKET and ex.placed[0].reduce_only  # live close


def test_runner_opens_then_resolves(tmp_path):
    # Fire on a flat market, then a later pass with price through TP resolves the trade.
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks({"BTC": 100.0}), state=state)
    state.enqueue(Candidate(id="a", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90, created_at=NOW))
    run_once(ex, state, caps(), clamp(TunableConfig()), decide_fn=act_now, now=NOW)
    assert len(state.open_trades()) == 1 and len(ex.get_positions()) == 1

    ex2 = PaperExchange(10_000.0, marks=FakeMarks({"BTC": 130.0}), state=state)
    s = run_once(ex2, state, caps(), clamp(TunableConfig()), decide_fn=act_now, now=NOW + 60)
    assert s.resolved == 1
    assert state.resolved_trades()[0]["status"] == "won"
    assert ex2.get_positions() == []
