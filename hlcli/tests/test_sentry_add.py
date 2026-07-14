"""Sentry 6d: ADD (pyramiding) — the one risk-increasing action — and the
graduation gate on mainnet management.

The pyramid discipline under test: winners only (≥ min R), the stop rises in the
same action and FIRST (no new size before the old risk shrinks), the code sizes
the add (profit-covered, ≤ half the position, entry caps re-run on the total),
a lifetime per-coin add budget, and a slice whose protection fails is
emergency-closed and booked `aborted`.
"""

import json

from hlcli.core.types import Network, OrderType, Side
from hlcli.sentry.decision import validate_management
from hlcli.sentry.gate import AddTo, ManageGateContext, evaluate_management
from hlcli.sentry.live import graduation_for_management, manage_live
from hlcli.state.store import StateStore, open_state
from hlcli.tests._helpers import caps, tunable
from hlcli.tests.test_sentry_live import (
    FakeLive,
    ScriptedManager,
    _fast_caps,
    _paper,
    _payload,
    _resting,
    _trade,
)

NOW = 1_000_000.0


def _decision(**over):
    d = validate_management(_payload("add", **{"new_stop": 105.0, **over}), 1)
    assert d is not None
    return d


def _add_caps(**kw):
    """ADD is disabled by default (cap 0, audit L-3) — these tests exercise the add
    mechanics, so they opt into a budget explicitly."""
    return caps(sentry_max_adds_per_position=2, **kw)


def _gctx(**over):
    base = dict(caps=_add_caps(), tunable=tunable(), mark=112.0, now=NOW,
                breaker_tripped=False, daily_loss_hit=False,
                last_applied_ts=None, actions_today=0, last_bank_ts=None, last_extend_ts=None,
                equity=10_000.0, coin_adds=0, coin_size=0.0)
    return ManageGateContext(**{**base, **over})


# --- validation ---------------------------------------------------------------------


def test_add_requires_a_raised_stop_price():
    assert validate_management(_payload("add", new_stop=0), 1) is None
    assert validate_management(_payload("add", new_stop=float("nan")), 1) is None
    assert validate_management(_payload("add", new_stop=105.0), 1).new_stop == 105.0


# --- gate ---------------------------------------------------------------------------


def test_add_rejected_when_halted():
    out = evaluate_management(_decision(), _trade(size=2.0), _gctx(breaker_tripped=True))
    assert not out.approved and "halted" in out.reason


def test_add_only_to_winners_at_min_r():
    out = evaluate_management(_decision(new_stop=100.5), _trade(), _gctx(mark=105.0))  # +0.5R
    assert not out.approved and "winners" in out.reason


def test_add_budget_is_per_coin_lifetime():
    out = evaluate_management(_decision(), _trade(),
                              _gctx(coin_adds=_add_caps().sentry_max_adds_per_position))
    assert not out.approved and "add budget" in out.reason


def test_add_is_disabled_by_default():
    # The default cap is 0 (audit L-3): with stock caps the one risk-increasing action
    # is always rejected, however perfect the setup — enabling it is a deliberate choice.
    out = evaluate_management(_decision(), _trade(size=2.0), _gctx(caps=caps()))
    assert not out.approved and "add budget" in out.reason


def test_add_must_raise_the_stop_off_the_mark():
    out = evaluate_management(_decision(new_stop=90.0), _trade(), _gctx())  # unchanged stop
    assert not out.approved and "raise" in out.reason
    out = evaluate_management(_decision(new_stop=112.0), _trade(), _gctx(mark=112.0))
    assert not out.approved and "instantly" in out.reason


def test_add_sized_by_half_the_position():
    # entry 100 / initial sl 90 / size 2 / mark 112 (+1.2R) / raised stop 105:
    # by_profit = 24/7 ≈ 3.43, by_half = 1.0, caps roomy ⇒ half binds.
    out = evaluate_management(_decision(), _trade(size=2.0), _gctx())
    assert out.approved and isinstance(out.plan, AddTo)
    assert out.plan.size == 1.0 and out.plan.new_stop == 105.0


def test_add_sized_by_unrealized_profit_coverage():
    # min_r lowered so a shallow winner qualifies: favorable 3, gap 8 ⇒
    # by_profit = 6/8 = 0.75 < by_half = 1.0.
    c = _add_caps(sentry_add_min_r=0.2)
    out = evaluate_management(_decision(new_stop=95.0), _trade(size=2.0),
                              _gctx(caps=c, mark=103.0))
    assert out.approved and out.plan.size == 0.75


def test_add_recleared_against_entry_caps_on_total_size():
    # Notional cap: 250/112 − 2 ≈ 0.232 binds below the half-size 1.0.
    out = evaluate_management(_decision(), _trade(size=2.0),
                              _gctx(caps=_add_caps(max_notional_per_trade=250.0)))
    assert out.approved and round(out.plan.size, 3) == 0.232
    # No room at all ⇒ reject, never a negative/zero order.
    out = evaluate_management(_decision(), _trade(size=2.0),
                              _gctx(caps=_add_caps(max_notional_per_trade=200.0)))
    assert not out.approved and "no room" in out.reason


def test_add_counts_sibling_rows_via_coin_size():
    # A prior add means this row's size understates the position: held=8 ⇒
    # by_notional = 1000/112 − 8 ≈ 0.929 binds (by_half would be 4).
    out = evaluate_management(_decision(), _trade(size=2.0), _gctx(coin_size=8.0))
    assert out.approved and round(out.plan.size, 3) == 0.929


# --- apply on paper -----------------------------------------------------------------


def test_paper_add_raises_stop_and_books_child_slice(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=2), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("add", new_stop=105.0)))
    assert s.applied == 1 and s.failed == 0

    parent, child = state.open_trades()
    assert parent["sl"] == 105.0                      # raised with the add
    assert child["entry"] == 112.0 and child["size"] == 1.0
    assert child["sl"] == 105.0 and child["initial_sl"] == 105.0  # honest R basis
    assert child["tp"] == 130.0 and child["conviction"] == 0.7

    pos = ex.get_positions()[0]
    assert pos.size == 3.0 and pos.entry_price == 104.0  # blended (2@100 + 1@112)

    actions = {r["action"] for r in state.recent_sentry()}
    assert {"managed_add", "move_stop"} <= actions
    add_row = [r for r in state.recent_sentry() if r["action"] == "managed_add"][0]
    assert json.loads(add_row["details"])["child_trade_id"] == child["id"]


def test_add_idempotency_key_prevents_double_fire(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)
    state.record_fire(f"sentry:add:{tid}:0", None, NOW)  # crash left the key behind

    manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=2), tunable(), now=NOW,
                decide_fn=ScriptedManager(_payload("add", new_stop=105.0)))
    assert len(state.open_trades()) == 1              # no child slice
    assert ex.get_positions()[0].size == 2.0          # no market order
    assert state.open_trades()[0]["sl"] == 105.0      # the raise still applied — safe direction


def test_add_budget_counts_only_this_positions_adds(tmp_path):
    # A `managed_add` from a PRIOR (since-closed) BTC position must not consume this
    # fresh position's add budget — the cap is per open position, not per coin forever.
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.log_sentry(NOW - 1000, 999, "BTC", "managed_add", {})  # a prior position's add
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=1), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("add", new_stop=105.0)))
    assert s.applied == 1 and len(state.open_trades()) == 2  # the add went through


# --- apply on a live backend ----------------------------------------------------------


def test_live_add_sequences_raise_fire_protect(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0), _resting(2, "take profit market", 130.0)])

    s = manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=2), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("add", new_stop=110.0)))
    assert s.applied == 1
    kinds = [(o.order_type, o.size) for o in ex.placed]
    assert kinds == [
        (OrderType.STOP_LOSS, 1.0),    # 1. raise the whole position's stop FIRST
        (OrderType.MARKET, 0.5),       # 2. the add (half of size 1)
        (OrderType.STOP_LOSS, 0.5),    # 3. protect the slice…
        (OrderType.TAKE_PROFIT, 0.5),  # 4. …both triggers
    ]
    assert not ex.placed[1].reduce_only and ex.placed[2].reduce_only
    assert ex.canceled == [1]          # only the OLD stop; the position TP stays
    assert len(state.open_trades()) == 2


def test_live_add_aborts_if_stop_raise_rejected(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0)], reject=(OrderType.STOP_LOSS,))

    s = manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=2), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("add", new_stop=110.0)))
    assert s.applied == 0 and s.failed == 1
    assert all(o.order_type is not OrderType.MARKET for o in ex.placed)  # no size w/o the raise
    assert len(state.open_trades()) == 1 and state.open_trades()[0]["sl"] == 90.0


def test_live_add_slice_protection_failure_emergency_closes(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0)], reject=(OrderType.TAKE_PROFIT,))

    s = manage_live(ex, state, _fast_caps(sentry_max_adds_per_position=2), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("add", new_stop=110.0)))
    # The raise applied (the stop genuinely moved — the safe direction); the add failed.
    assert s.applied == 1 and s.failed == 1
    aborted = [t for t in state.resolved_trades() if t["status"] == "aborted"]
    assert len(aborted) == 1 and aborted[0]["size"] == 0.5
    # The emergency close is the last order and reduce-only; the slice SL was canceled.
    assert ex.placed[-1].order_type is OrderType.MARKET and ex.placed[-1].reduce_only
    assert len(ex.canceled) == 2  # old position stop (raise) + the placed slice SL


# --- graduation gate for mainnet management -------------------------------------------


def test_mainnet_management_graduation(tmp_path):
    c = caps(data_dir=tmp_path)
    assert graduation_for_management(c)["ready"] is False  # empty testnet book

    state = open_state(c, Network.TESTNET)
    day = 86_400.0
    for i in range(25):  # 25 winners spread over 8 days
        tid = state.open_trade(f"c{i}", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8,
                               None, NOW + i * day / 3)
        state.resolve_trade(tid, "won", 120.0, 20.0, 2.0, NOW + i * day / 3 + 60)
    state.close()

    verdict = graduation_for_management(c)
    assert verdict["ready"] is True and verdict["n"] == 25
