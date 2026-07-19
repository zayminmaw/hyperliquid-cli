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


def _live_position(coin="BTC", side=Side.LONG, size=1.0, entry=100.0):
    from hlcli.core.types import Position

    return Position(coin=coin, side=side, size=size, entry_price=entry)


def test_native_protected_books_actual_close_fill_not_the_level(tmp_path):
    # On a live backend the resolver's market close fills with slippage; the ledger
    # must record that fill (here 88, not the 90 stop) so expectancy stays honest.
    from hlcli.core.types import Network, OrderType
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 85.0}, fill_price=88.0,
                          positions=[_live_position()])
    _open(state)  # long, entry 100, sl 90
    n = resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                            marks={"BTC": 85.0}, native_protected=True)
    assert n == 1
    t = state.resolved_trades()[0]
    assert t["status"] == "lost" and t["exit_price"] == 88.0
    assert t["realized"] == -12.0  # (88 - 100) * 1.0, not the idealized (90-100)
    assert ex.placed[0].order_type is OrderType.MARKET and ex.placed[0].reduce_only  # live close


def test_vanished_position_is_resolved_from_candle_extremes(tmp_path):
    # A native SL fired on a wick; the mark recovered before this pass. The exchange
    # is flat, the ledger says open — the candle low proves the stop was hit.
    from hlcli.core.types import Candle, Network
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 100.0}, positions=[])  # flat
    ex.get_candles = lambda coin, **kw: [
        Candle(t=int(NOW * 1000), o=100, h=101, l=89.0, c=100, v=1),  # wick through sl=90
    ]
    _open(state)  # long, entry 100, sl 90 — mark 100 says "still live"
    n = resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                            marks={"BTC": 100.0}, native_protected=True)
    assert n == 1
    t = state.resolved_trades()[0]
    assert t["status"] == "lost" and t["exit_price"] == 90.0
    assert ex.placed == []  # nothing to close — the position was already gone


def test_vanished_position_with_no_level_touched_books_external_close(tmp_path):
    from hlcli.core.types import Network
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 103.0}, positions=[])
    _open(state)
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                        marks={"BTC": 103.0}, native_protected=True)
    t = state.resolved_trades()[0]
    assert t["status"] == "closed" and t["exit_price"] == 103.0  # manual close at mark


def test_vanished_position_books_the_real_closing_fill_not_the_mark(tmp_path):
    # Item L: a native trigger / manual close filled at 88.5; the mark has since moved to
    # 103. Without the fill lookup the ledger would book the 103 mark; it must book 88.5.
    from hlcli.core.types import Fill, Network
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 103.0}, positions=[], fills=[
        Fill(coin="BTC", px=88.5, size=1.0, dir="Close Long", closed_pnl=-11.5, fee=0.04,
             time_ms=int(NOW * 1000) + 1),
    ])
    _open(state)  # long, entry 100, opened at NOW
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                        marks={"BTC": 103.0}, native_protected=True)
    t = state.resolved_trades()[0]
    assert t["exit_price"] == 88.5      # the real closing fill, not the 103 mark
    assert t["realized"] == -11.5       # (88.5 - 100) * 1.0


def test_vanished_scaled_trade_keeps_the_mark_estimate(tmp_path):
    # A scaled trade's earlier scale-out fills would blend into the average, so L is
    # skipped and the mark/level estimate stands.
    from hlcli.core.types import Fill, Network
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    tid = _open(state)
    state.split_trade(tid, 0.5, 105.0, 2.5, 0.5, NOW)  # parent now scaled_out=1, remainder 0.5
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 103.0}, positions=[], fills=[
        Fill(coin="BTC", px=88.5, size=0.5, dir="Close Long", time_ms=int(NOW * 1000) + 1),
    ])
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                        marks={"BTC": 103.0}, native_protected=True)
    t = [r for r in state.resolved_trades() if r["id"] == tid][0]
    assert t["exit_price"] == 103.0     # mark estimate kept — the fill lookup is skipped


def test_live_close_cancels_the_surviving_trigger(tmp_path):
    # After the SL side of the pair closes the trade, the orphaned TP trigger must
    # be cancelled or it will close the NEXT position in this coin.
    from hlcli.core.types import Network, OpenOrder
    from hlcli.tests.test_protect import FakeLiveExchange

    state = StateStore(tmp_path / "state.db")
    surviving_tp = OpenOrder(coin="BTC", oid=77, side=Side.SHORT, size=1.0, price=120.0,
                             order_type="take profit market", reduce_only=True, is_trigger=True)
    ex = FakeLiveExchange(Network.MAINNET, marks={"BTC": 85.0}, fill_price=88.0,
                          positions=[_live_position()], open_orders=[surviving_tp])
    _open(state)
    resolve_open_trades(ex, state, caps(), clamp(TunableConfig()), NOW,
                        marks={"BTC": 85.0}, native_protected=True)
    assert ("BTC", 77) in ex.canceled


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
