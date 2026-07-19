"""Sentry 6a: the deterministic trail engine and its apply layer.

The invariants under test are the money-guarding ones: a stop only ever ratchets
toward profit, a proposed stop never sits at/past the mark, dust moves are
suppressed, scale-out happens exactly once and is idempotent across a crash, a
shadow pass never touches real trades, and the live stop replacement is
place-new-then-cancel-old.
"""

import sqlite3

from hlcli.core.config_schema import TrailConfig, TunableConfig, clamp
from hlcli.core.types import Candle, Network, OpenOrder, OrderResult, OrderType, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.executor.resolve import resolve_open_trades
from hlcli.executor.runner import run_once
from hlcli.sentry.apply import ManageSummary, apply_close, apply_move_stop, manage_open_trades
from hlcli.sentry.engine import MoveStop, ScaleOut, active, atr, plan
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, caps, tunable

NOW = 1_000_000.0


def _trade(*, side="long", entry=100.0, sl=90.0, initial_sl=None, tp=130.0, size=1.0,
           scaled_out=0, trade_id=1, shadow=0):
    return {"id": trade_id, "coin": "BTC", "side": side, "entry": entry, "sl": sl,
            "initial_sl": initial_sl if initial_sl is not None else sl, "tp": tp,
            "size": size, "scaled_out": scaled_out, "shadow": shadow}


def _cfg(**kw) -> TrailConfig:
    return clamp(TunableConfig(trail=TrailConfig(**kw))).trail


def _bars(prices, spread=1.0):
    """Flat-ish candles: each bar closes at `p` with a `spread`-wide range → ATR ≈ spread."""
    return [Candle(t=i, o=p, h=p + spread / 2, l=p - spread / 2, c=p, v=1.0)
            for i, p in enumerate(prices)]


# --- engine: activation -----------------------------------------------------------


def test_defaults_are_inert():
    cfg = tunable().trail
    assert not active(cfg)
    assert plan(_trade(), 120.0, [], cfg) == []


def test_any_rule_activates():
    assert active(_cfg(breakeven_trigger_r=1.0))
    assert active(_cfg(style="percent"))
    assert active(_cfg(scale_out_r=1.0))


# --- engine: breakeven ratchet ----------------------------------------------------


def test_breakeven_moves_stop_at_trigger_r():
    cfg = _cfg(breakeven_trigger_r=1.0, breakeven_buffer_r=0.05)
    # risk = 10; mark 110 = +1R exactly
    (move,) = plan(_trade(), 110.0, [], cfg)
    assert isinstance(move, MoveStop) and move.reason == "breakeven"
    assert move.new_sl == 100.0 + 0.05 * 10


def test_breakeven_not_before_trigger():
    cfg = _cfg(breakeven_trigger_r=1.0)
    assert plan(_trade(), 109.0, [], cfg) == []  # +0.9R only


def test_breakeven_short_side():
    cfg = _cfg(breakeven_trigger_r=1.0, breakeven_buffer_r=0.1)
    t = _trade(side="short", entry=100.0, sl=110.0, tp=70.0)
    (move,) = plan(t, 90.0, [], cfg)  # +1R for a short
    assert move.new_sl == 100.0 - 0.1 * 10


def test_stop_never_widens():
    cfg = _cfg(breakeven_trigger_r=1.0)
    # Stop already ratcheted above the breakeven level — proposal would widen it.
    t = _trade(sl=105.0, initial_sl=90.0)
    assert plan(t, 112.0, [], cfg) == []


def test_dust_move_suppressed():
    cfg = _cfg(breakeven_trigger_r=1.0, breakeven_buffer_r=0.05, min_move_r=0.1)
    # Stop sits at 100.0; breakeven proposal is 100.5 — an 0.05R nudge, below min_move_r.
    t = _trade(sl=100.0, initial_sl=90.0)
    assert plan(t, 112.0, [], cfg) == []


def test_proposal_at_or_past_mark_suppressed():
    # +0.4R clears a 0.3R trigger, but entry + 0.5R buffer = 100.5 ≥ mark 100.4.
    cfg = _cfg(breakeven_trigger_r=0.3, breakeven_buffer_r=0.5, min_move_r=0.0)
    assert plan(_trade(entry=100.0, sl=99.0), 100.4, [], cfg) == []


# --- engine: trailing -------------------------------------------------------------


def test_percent_trail():
    cfg = _cfg(style="percent", trail_percent=1.0, trail_start_r=1.0, min_move_r=0.0)
    (move,) = plan(_trade(), 120.0, [], cfg)
    assert move.reason == "trail" and move.new_sl == 120.0 - 1.2  # 1% of the mark


def test_trail_waits_for_start_r():
    cfg = _cfg(style="percent", trail_percent=1.0, trail_start_r=2.0, min_move_r=0.0)
    assert plan(_trade(), 110.0, [], cfg) == []  # +1R < trail_start_r


def test_atr_math_and_trail():
    bars = _bars([100.0] * 20, spread=2.0)
    assert atr(bars) == 2.0
    cfg = _cfg(style="atr", atr_multiple=2.0, trail_start_r=1.0, min_move_r=0.0)
    (move,) = plan(_trade(), 120.0, bars, cfg)
    assert move.new_sl == 120.0 - 4.0


def test_atr_needs_history():
    assert atr(_bars([100.0] * 10)) is None
    cfg = _cfg(style="atr", trail_start_r=0.0)
    assert plan(_trade(), 120.0, _bars([100.0] * 5), cfg) == []  # missing data never moves a stop


def test_tightest_proposal_wins():
    # At +2R with a close percent trail, the trail (118.8) beats breakeven (100.5).
    cfg = _cfg(style="percent", trail_percent=1.0, trail_start_r=1.0,
               breakeven_trigger_r=1.0, breakeven_buffer_r=0.05, min_move_r=0.0)
    (move,) = plan(_trade(), 120.0, [], cfg)
    assert move.reason == "trail" and move.new_sl == 118.8


# --- engine: scale-out ------------------------------------------------------------


def test_scale_out_at_ladder():
    cfg = _cfg(scale_out_r=1.0, scale_out_fraction=0.5)
    scale, = [a for a in plan(_trade(size=2.0), 110.0, [], cfg) if isinstance(a, ScaleOut)]
    assert scale.size == 1.0 and scale.level == 110.0 and scale.r == 1.0


def test_scale_out_only_once():
    cfg = _cfg(scale_out_r=1.0)
    assert plan(_trade(scaled_out=1), 115.0, [], cfg) == []


def test_scale_out_short_level():
    cfg = _cfg(scale_out_r=1.0, scale_out_fraction=0.5)
    t = _trade(side="short", entry=100.0, sl=110.0, tp=70.0, size=2.0)
    (scale,) = plan(t, 90.0, [], cfg)
    assert scale.level == 90.0


def test_scale_out_precedes_stop_move():
    cfg = _cfg(scale_out_r=1.0, breakeven_trigger_r=1.0, min_move_r=0.0)
    actions = plan(_trade(size=2.0), 110.0, [], cfg)
    assert isinstance(actions[0], ScaleOut) and isinstance(actions[1], MoveStop)


# --- clamp ------------------------------------------------------------------------


def test_clamp_trail_bounds():
    t = _cfg(style="fibonacci", atr_multiple=float("nan"), scale_out_fraction=0.99,
             breakeven_trigger_r=-3.0)
    assert t.style == "off"                # unknown style → default
    assert t.atr_multiple == 2.0           # NaN → field default, never the widest bound
    assert t.scale_out_fraction == 0.9
    assert t.breakeven_trigger_r == 0.0


# --- store: migration + split ----------------------------------------------------


def test_migration_backfills_initial_sl(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT NOT NULL,"
        " coin TEXT NOT NULL, side TEXT NOT NULL, entry REAL NOT NULL, sl REAL NOT NULL,"
        " tp REAL NOT NULL, size REAL NOT NULL, conviction REAL NOT NULL, regime TEXT,"
        " opened_at REAL NOT NULL, status TEXT NOT NULL DEFAULT 'open', exit_price REAL,"
        " realized REAL, r_multiple REAL, closed_at REAL)"
    )
    conn.execute(
        "INSERT INTO trades(candidate_id, coin, side, entry, sl, tp, size, conviction, opened_at)"
        " VALUES('c1','BTC','long',100,90,120,1.0,0.8,?)", (NOW,)
    )
    conn.commit()
    conn.close()

    t = StateStore(db).open_trades()[0]
    assert t["initial_sl"] == 90 and t["scaled_out"] == 0 and t["shadow"] == 0


def test_split_trade_books_child_and_shrinks_parent(tmp_path):
    state = StateStore(tmp_path / "s.db")
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)
    child = state.split_trade(tid, 1.0, 110.0, 10.0, 1.0, NOW + 60)

    parent = state.open_trades()[0]
    assert parent["size"] == 1.0 and parent["scaled_out"] == 1
    row = [t for t in state.resolved_trades() if t["id"] == child][0]
    assert row["status"] == "scaled" and row["size"] == 1.0 and row["realized"] == 10.0
    assert row["initial_sl"] == 90.0


def test_partial_pnl_nets_the_taker_fee():
    # Wave-2 K follow-on: sentry scale-out/close book realized + R net of the taker fee,
    # exactly like the resolver, so cohorts stay consistent when sentry is enabled.
    import pytest

    from hlcli.sentry.apply import _partial_pnl

    trade = {"side": "long", "entry": 100.0, "initial_sl": 90.0, "sl": 90.0}
    assert _partial_pnl(trade, 1.0, 120.0, 0.0) == (20.0, 2.0, 0.0)  # fee off ⇒ gross
    net, r, fee = _partial_pnl(trade, 1.0, 120.0, 0.045)  # close 1.0 @ 120, dollar-risk 10
    expect_fee = 0.045 / 100 * 1.0 * (100 + 120)
    assert fee == pytest.approx(expect_fee)
    assert net == round(20 - expect_fee, 6) and r == round((20 - expect_fee) / 10, 4)


def test_split_trade_records_fee_paid_on_the_child(tmp_path):
    state = StateStore(tmp_path / "s.db")
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)
    child = state.split_trade(tid, 1.0, 110.0, 9.9, 0.99, NOW + 60, 0.1)  # net realized + fee
    row = [t for t in state.resolved_trades() if t["id"] == child][0]
    assert row["realized"] == 9.9 and row["fee_paid"] == 0.1


def test_sentry_log_roundtrip(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.log_sentry(NOW, 1, "BTC", "move_stop", {"from": 90.0, "to": 100.5})
    (row,) = state.recent_sentry()
    assert row["action"] == "move_stop" and row["coin"] == "BTC"


# --- apply: paper -----------------------------------------------------------------


def _paper(tmp_path, marks):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    return state, ex


def _manage(ex, state, trail: TrailConfig, *, now=NOW, **kw):
    cfg = tunable().model_copy(update={"trail": trail})
    return manage_open_trades(ex, state, cfg, now, **kw)


def test_paper_breakeven_updates_ledger(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)

    s = _manage(ex, state, _cfg(breakeven_trigger_r=1.0, breakeven_buffer_r=0.05))
    assert s.stops_moved == 1
    t = state.open_trades()[0]
    assert t["sl"] == 100.5 and t["initial_sl"] == 90.0
    assert state.recent_sentry()[0]["trade_id"] == tid
    # Second pass, same conditions: the ratchet + dust guard makes it a no-op.
    assert _manage(ex, state, _cfg(breakeven_trigger_r=1.0, breakeven_buffer_r=0.05)).stops_moved == 0


def test_paper_scale_out_splits_ledger_and_book(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 111.0})
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)

    s = _manage(ex, state, _cfg(scale_out_r=1.0, scale_out_fraction=0.5))
    assert s.scaled_out == 1
    assert ex.get_positions()[0].size == 1.0            # book shrank by the fraction
    assert state.paper_realized() == 10.0               # (110-100) × 1.0, booked at the ladder
    parent = state.open_trades()[0]
    assert parent["size"] == 1.0 and parent["scaled_out"] == 1
    child = [t for t in state.resolved_trades() if t["status"] == "scaled"][0]
    assert child["realized"] == 10.0 and child["r_multiple"] == 1.0
    # Once only — the flag survives into the next pass.
    assert _manage(ex, state, _cfg(scale_out_r=1.0)).scaled_out == 0
    assert state.already_fired(f"sentry:scale:{tid}")


def test_scale_out_idempotent_after_crash(tmp_path):
    """Key recorded, crash before the ledger split: a rerun must not close again."""
    state, ex = _paper(tmp_path, {"BTC": 111.0})
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)
    state.record_fire(f"sentry:scale:{tid}", None, NOW)

    s = _manage(ex, state, _cfg(scale_out_r=1.0))
    assert s.scaled_out == 0 and ex.get_positions()[0].size == 2.0


def test_shadow_pass_manages_only_shadow_rows(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0, "ETH": 1800.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    state.open_trade("c2", "ETH", Side.LONG, 1500.0, 1400.0, 2000.0, 1.0, 0.8, None, NOW, shadow=True)

    s = _manage(ex, state, _cfg(breakeven_trigger_r=1.0), shadow_only=True)
    assert s.stops_moved == 1
    by_coin = {t["coin"]: t for t in state.open_trades()}
    assert by_coin["ETH"]["sl"] > 1400.0     # shadow row managed, orderlessly
    assert by_coin["BTC"]["sl"] == 90.0      # real row untouched by a shadow pass


def test_shadow_scale_out_is_orderless(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 111.0})
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW, shadow=True)

    s = _manage(ex, state, _cfg(scale_out_r=1.0), shadow_only=True)
    assert s.scaled_out == 1
    assert ex.get_positions() == [] and state.paper_realized() == 0.0
    child = [t for t in state.resolved_trades() if t["status"] == "scaled"][0]
    assert child["shadow"] == 1 and child["realized"] == 10.0


def test_dry_run_previews_without_mutating(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)

    s = _manage(ex, state, _cfg(breakeven_trigger_r=1.0, scale_out_r=1.0), dry_run=True)
    assert {a["action"] for a in s.actions} == {"move_stop", "scale_out"}
    assert s.stops_moved == 0 and s.scaled_out == 0
    t = state.open_trades()[0]
    assert t["sl"] == 90.0 and t["size"] == 2.0 and state.recent_sentry() == []


# --- apply: live trigger sync -----------------------------------------------------


class FakeLive:
    """Live-backend stand-in for the stop replacement: resting triggers + cancel log."""

    network = Network.TESTNET

    def __init__(self, open_orders, *, reject_stop=False):
        self.open_orders = open_orders
        self.reject_stop = reject_stop
        self.placed = []
        self.canceled = []

    def get_marks(self):
        return {"BTC": 112.0}

    def get_candles(self, coin, *, interval="15m", lookback=48):
        return []

    def get_open_orders(self):
        return list(self.open_orders)

    def place_order(self, order):
        self.placed.append(order)
        if self.reject_stop and order.order_type is OrderType.STOP_LOSS:
            return OrderResult(accepted=False, status="error", message="rejected")
        return OrderResult(accepted=True, status="resting", order_id=str(100 + len(self.placed)),
                           filled_size=order.size, avg_price=None)

    def cancel(self, coin, oid):
        self.canceled.append(oid)
        return OrderResult(accepted=True, status="canceled")


def _resting(oid, order_type, price):
    return OpenOrder(coin="BTC", oid=oid, side=Side.SHORT, size=1.0, price=price,
                     order_type=order_type, reduce_only=True, is_trigger=True)


def test_live_stop_replacement_places_then_cancels(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0), _resting(2, "take profit market", 130.0)])

    s = _manage(ex, state, _cfg(breakeven_trigger_r=1.0), native_protected=True)
    assert s.stops_moved == 1
    (placed,) = ex.placed
    assert placed.order_type is OrderType.STOP_LOSS and placed.trigger_price == 100.5
    assert placed.reduce_only and placed.side is Side.SHORT
    assert ex.canceled == [1]                       # the old stop — never the take-profit
    assert state.open_trades()[0]["sl"] == 100.5


def test_live_stop_rejection_keeps_old_level(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0)], reject_stop=True)

    s = _manage(ex, state, _cfg(breakeven_trigger_r=1.0), native_protected=True)
    assert s.stops_moved == 0 and s.failed == 1
    assert ex.canceled == []                        # old protection stays put
    assert state.open_trades()[0]["sl"] == 90.0     # ledger agrees with the exchange


# --- apply: slice-scoped trigger cancellation (a coin with sibling rows) -------------


def _sibling_book(tmp_path):
    """A coin with two ledger rows (a parent + an added slice), each carrying its own
    recorded SL/TP oids — the post-ADD shape earlier code stripped."""
    state = StateStore(tmp_path / "s.db")
    ex = FakeLive([_resting(1, "stop market", 90.0), _resting(2, "take profit market", 130.0),
                   _resting(3, "stop market", 95.0), _resting(4, "take profit market", 130.0)])
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW,
                     sl_oid="1", tp_oid="2")
    child = state.open_trade("c1", "BTC", Side.LONG, 105.0, 95.0, 130.0, 0.5, 0.7, None, NOW,
                             sl_oid="3", tp_oid="4")
    return state, ex, child


def test_tighten_cancels_only_this_rows_stop_not_a_sibling(tmp_path):
    state, ex, child = _sibling_book(tmp_path)
    parent = dict(state.open_trades()[0])
    apply_move_stop(ex, state, parent, MoveStop(new_sl=101.0, reason="llm"), NOW,
                    native_protected=True, summary=ManageSummary(), alerter=None)
    assert ex.canceled == [1]                          # the parent's old stop ONLY
    rows = {t["id"]: t for t in state.open_trades()}
    assert rows[child]["sl_oid"] == "3"                # the slice's stop is untouched
    assert rows[parent["id"]]["sl_oid"] == "101"       # parent now points at the new trigger


def test_close_cancels_only_this_rows_pair_keeping_the_slice(tmp_path):
    state, ex, child = _sibling_book(tmp_path)
    parent = dict(state.open_trades()[0])
    apply_close(ex, state, parent, 95.0, NOW, native_protected=True,
                summary=ManageSummary(), alerter=None)
    assert sorted(ex.canceled) == [1, 2]               # the parent's pair; the slice's 3/4 survive
    remaining = state.open_trades()
    assert len(remaining) == 1 and remaining[0]["id"] == child


# --- resolver interplay -----------------------------------------------------------


def test_ratcheted_stop_resolves_as_win_with_initial_r(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 104.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    state.update_trade_sl(tid, 105.0)  # sentry trailed the stop above entry

    resolve_open_trades(ex, state, caps(), tunable(), NOW + 60, marks={"BTC": 104.0})
    (t,) = state.resolved_trades()
    assert t["status"] == "won"          # a profitable stop-out is not a loss
    assert t["exit_price"] == 105.0
    assert t["r_multiple"] == 0.5        # R against the INITIAL 10-point risk, not the trailed 5


def test_losing_stop_still_books_lost(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 89.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    resolve_open_trades(ex, state, caps(), tunable(), NOW + 60, marks={"BTC": 89.0})
    assert state.resolved_trades()[0]["status"] == "lost"


# --- runner integration -----------------------------------------------------------


def test_run_once_manages_before_resolving(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    cfg = tunable().model_copy(update={"trail": _cfg(breakeven_trigger_r=1.0)})

    summary = run_once(ex, state, caps(), cfg, now=NOW + 60)
    assert summary.managed == 1
    t = state.open_trades()[0]
    assert t["sl"] == 100.5              # tightened, and NOT closed by this pass's resolve
    assert summary.resolved == 0


def test_run_once_inert_trail_manages_nothing(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    summary = run_once(ex, state, caps(), tunable(), now=NOW + 60)
    assert summary.managed == 0 and state.open_trades()[0]["sl"] == 90.0
