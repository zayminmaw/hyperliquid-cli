"""Sentry 6c: the management gate (first-failure, risk only goes down) and the
live pass — gated LLM actions applied to the paper book / a fake live backend,
churn caps enforced from the sentry log, real trades only."""

import json

import pytest

from hlcli.core.types import Network, OpenOrder, OrderResult, OrderType, Side
from hlcli.exchange.paper import PaperExchange
from hlcli.sentry.decision import ManagementResult, validate_management
from hlcli.sentry.engine import MoveStop, ScaleOut
from hlcli.sentry.gate import CloseAll, ManageGateContext, MoveTP, evaluate_management
from hlcli.sentry.live import manage_live
from hlcli.state.store import StateStore
from hlcli.tests._helpers import FakeMarks, caps, tunable

NOW = 1_000_000.0


def _payload(action="hold", **over):
    return {"action": action, "confidence": 0.7, "rationale": "because",
            "new_stop": 0, "reduce_pct": 0, "new_tp": 0, **over}


def _decision(action="hold", **over):
    d = validate_management(_payload(action, **over), 1)
    assert d is not None
    return d


def _trade(*, side="long", entry=100.0, sl=90.0, initial_sl=None, tp=130.0, size=2.0,
           scaled_out=0, shadow=0):
    return {"id": 1, "coin": "BTC", "side": side, "entry": entry, "sl": sl,
            "initial_sl": initial_sl if initial_sl is not None else sl, "tp": tp,
            "size": size, "scaled_out": scaled_out, "shadow": shadow, "opened_at": NOW}


def _gctx(**over):
    base = dict(caps=caps(), tunable=tunable(), mark=110.0, now=NOW,
                breaker_tripped=False, daily_loss_hit=False,
                last_applied_ts=None, actions_today=0, last_bank_ts=None, last_extend_ts=None)
    return ManageGateContext(**{**base, **over})


# --- gate: general ------------------------------------------------------------------


def test_hold_passes_with_no_plan():
    out = evaluate_management(_decision("hold"), _trade(), _gctx())
    assert out.approved and out.plan is None


def test_halted_allows_only_risk_reduction():
    ctx = _gctx(breaker_tripped=True)
    assert evaluate_management(_decision("tighten_stop", new_stop=100.0), _trade(), ctx).approved
    assert evaluate_management(_decision("close"), _trade(), ctx).approved
    out = evaluate_management(_decision("extend_tp", new_tp=135.0), _trade(sl=101.0), ctx)
    assert not out.approved and "halted" in out.reason


def test_action_budget_and_cooldown():
    out = evaluate_management(_decision("close"), _trade(),
                              _gctx(actions_today=caps().sentry_max_actions_per_position_per_day))
    assert not out.approved and "budget" in out.reason
    out = evaluate_management(_decision("close"), _trade(), _gctx(last_applied_ts=NOW - 60))
    assert not out.approved and "cooldown" in out.reason
    # Cooldown elapsed → fine.
    old = NOW - caps().sentry_min_action_interval_minutes * 60 - 1
    assert evaluate_management(_decision("close"), _trade(), _gctx(last_applied_ts=old)).approved


def test_opposing_window_blocks_flip_flops():
    out = evaluate_management(_decision("extend_tp", new_tp=135.0), _trade(sl=101.0),
                              _gctx(last_bank_ts=NOW - 600))
    assert not out.approved and "opposing" in out.reason
    out = evaluate_management(_decision("reduce", reduce_pct=50), _trade(),
                              _gctx(last_extend_ts=NOW - 600))
    assert not out.approved and "opposing" in out.reason


# --- gate: per-action checks --------------------------------------------------------


def test_tighten_must_actually_tighten():
    out = evaluate_management(_decision("tighten_stop", new_stop=85.0), _trade(), _gctx())
    assert not out.approved and "ratchet" in out.reason


def test_tighten_dust_and_mark_guards():
    # risk=10, min_move_r=0.1 ⇒ improvements under 1.0 are dust.
    out = evaluate_management(_decision("tighten_stop", new_stop=90.5), _trade(), _gctx())
    assert not out.approved and "dust" in out.reason
    out = evaluate_management(_decision("tighten_stop", new_stop=110.0), _trade(), _gctx(mark=110.0))
    assert not out.approved and "instantly" in out.reason


def test_tighten_approved_plans_move_stop():
    out = evaluate_management(_decision("tighten_stop", new_stop=101.0), _trade(), _gctx())
    assert out.approved and isinstance(out.plan, MoveStop)
    assert out.plan.new_sl == 101.0 and out.plan.reason == "llm"


def test_tighten_short_side():
    t = _trade(side="short", entry=100.0, sl=110.0, tp=70.0)
    out = evaluate_management(_decision("tighten_stop", new_stop=104.0), t, _gctx(mark=90.0))
    assert out.approved and out.plan.new_sl == 104.0
    assert not evaluate_management(_decision("tighten_stop", new_stop=115.0), t, _gctx(mark=90.0)).approved


def test_reduce_once_then_close_only():
    out = evaluate_management(_decision("reduce", reduce_pct=50), _trade(size=2.0), _gctx(mark=110.0))
    assert out.approved and isinstance(out.plan, ScaleOut)
    assert out.plan.size == 1.0 and out.plan.level == 110.0 and out.plan.r == 1.0
    out = evaluate_management(_decision("reduce", reduce_pct=50), _trade(scaled_out=1), _gctx())
    assert not out.approved and "already scaled" in out.reason


def test_close_always_plans_flatten():
    out = evaluate_management(_decision("close"), _trade(scaled_out=1), _gctx(mark=95.0))
    assert out.approved and isinstance(out.plan, CloseAll) and out.plan.level == 95.0


def test_extend_requires_breakeven_and_is_bounded():
    out = evaluate_management(_decision("extend_tp", new_tp=135.0), _trade(sl=95.0), _gctx())
    assert not out.approved and "breakeven" in out.reason
    protected = _trade(sl=100.5, initial_sl=90.0)  # ratcheted past breakeven; risk stays 10
    out = evaluate_management(_decision("extend_tp", new_tp=125.0), protected, _gctx())
    assert not out.approved and "would not extend" in out.reason
    out = evaluate_management(_decision("extend_tp", new_tp=145.0), protected, _gctx())
    assert not out.approved and "exceeds" in out.reason  # 15 > 1R (=10)
    out = evaluate_management(_decision("extend_tp", new_tp=138.0), protected, _gctx())
    assert out.approved and isinstance(out.plan, MoveTP) and out.plan.new_tp == 138.0


# --- live pass on paper ---------------------------------------------------------------


class ScriptedManager:
    """Injectable decide_fn: returns each payload in turn (last one repeats)."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0

    def __call__(self, ctx, caps_, tunable_):
        payload = self.payloads[min(self.calls, len(self.payloads) - 1)]
        self.calls += 1
        d = validate_management(payload, ctx.trade["id"])
        if d is None:
            return ManagementResult(None, payload if isinstance(payload, dict) else None, "schema_invalid")
        return ManagementResult(d, payload, "ok")


def _paper(tmp_path, marks):
    state = StateStore(tmp_path / "state.db")
    ex = PaperExchange(10_000.0, marks=FakeMarks(marks), state=state)
    return state, ex


def _fast_caps(**kw):
    """Caps with the churn throttles opened up so single-pass tests aren't spaced."""
    return caps(sentry_eval_interval_minutes=0, sentry_min_action_interval_minutes=0,
                sentry_opposing_window_minutes=0, **kw)


def test_live_tighten_applies_and_logs(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("tighten_stop", new_stop=105.0)))
    assert s.applied == 1 and s.evaluated == 1
    assert state.open_trades()[0]["sl"] == 105.0
    (row,) = state.recent_sentry()
    assert row["action"] == "managed_tighten_stop"
    detail = json.loads(row["details"])
    assert detail["confidence"] == 0.7 and detail["rationale"] == "because"


def test_live_reduce_banks_partial_loss_honestly(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 95.0})  # underwater: banking a partial LOSS
    state.upsert_paper_position("BTC", Side.LONG, 2.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 2.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("reduce", reduce_pct=50)))
    assert s.applied == 1
    assert ex.get_positions()[0].size == 1.0
    child = [t for t in state.resolved_trades() if t["status"] == "scaled"][0]
    assert child["realized"] == -5.0  # (95−100) × 1.0 — a scaled row can be a loss
    # …and the tuner must not count that as a win.
    from hlcli.tuner.stats import summary
    assert summary([child])["wins"] == 0
    assert summary([{**child, "realized": 5.0}])["wins"] == 1


def test_live_close_books_by_sign(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("close")))
    assert s.applied == 1 and ex.get_positions() == []
    (t,) = state.resolved_trades()
    assert t["status"] == "won" and t["exit_price"] == 112.0 and t["realized"] == 12.0


def test_live_close_at_loss_books_lost(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 94.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    manage_live(ex, state, _fast_caps(), tunable(), now=NOW,
                decide_fn=ScriptedManager(_payload("close")))
    assert state.resolved_trades()[0]["status"] == "lost"


def test_live_extend_after_breakeven(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 120.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    state.update_trade_sl(tid, 101.0)  # protected at breakeven

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW,
                    decide_fn=ScriptedManager(_payload("extend_tp", new_tp=138.0)))
    assert s.applied == 1 and state.open_trades()[0]["tp"] == 138.0


def test_live_hold_reject_drop_are_logged(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW, decide_fn=ScriptedManager(_payload("hold")))
    assert s.held == 1 and state.recent_sentry()[0]["action"] == "managed_hold"
    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW + 1,
                    decide_fn=ScriptedManager(_payload("tighten_stop", new_stop=80.0)))  # would widen
    assert s.rejected == 1 and state.recent_sentry()[0]["action"] == "managed_rejected"
    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW + 2,
                    decide_fn=ScriptedManager({"action": "panic"}))
    assert s.dropped == 1 and state.recent_sentry()[0]["action"] == "managed_dropped"


def test_eval_spacing_skips_recent_evaluations(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    fn = ScriptedManager(_payload("hold"))
    c = caps(sentry_eval_interval_minutes=15)

    manage_live(ex, state, c, tunable(), now=NOW, decide_fn=fn)
    s = manage_live(ex, state, c, tunable(), now=NOW + 60, decide_fn=fn)  # 1 min later
    assert s.spaced == 1 and s.evaluated == 0 and fn.calls == 1  # no second LLM call
    s = manage_live(ex, state, c, tunable(), now=NOW + 16 * 60, decide_fn=fn)
    assert s.evaluated == 1 and fn.calls == 2


def test_daily_llm_budget_halts_the_pass(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    fn = ScriptedManager(_payload("hold"))
    s = manage_live(ex, state, _fast_caps(sentry_max_llm_calls_per_day=0), tunable(),
                    now=NOW, decide_fn=fn)
    assert s.evaluated == 0 and fn.calls == 0 and "budget" in s.note


def test_cooldown_blocks_second_action(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.upsert_paper_position("BTC", Side.LONG, 1.0, 100.0)
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    c = caps(sentry_eval_interval_minutes=0, sentry_min_action_interval_minutes=30,
             sentry_opposing_window_minutes=0)
    fn = ScriptedManager(_payload("tighten_stop", new_stop=103.0),
                         _payload("tighten_stop", new_stop=106.0))

    assert manage_live(ex, state, c, tunable(), now=NOW, decide_fn=fn).applied == 1
    s = manage_live(ex, state, c, tunable(), now=NOW + 300, decide_fn=fn)  # 5 min later
    assert s.applied == 0 and s.rejected == 1
    assert json.loads(state.recent_sentry()[0]["details"])["reason"].startswith("cooldown")


def test_live_skips_shadow_book(tmp_path):
    state, ex = _paper(tmp_path, {"BTC": 112.0})
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW, shadow=True)
    fn = ScriptedManager(_payload("close"))
    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW, decide_fn=fn)
    assert s.evaluated == 0 and fn.calls == 0
    assert state.open_trades()[0]["status"] == "open"


# --- live backend: trigger replacement ------------------------------------------------


class FakeLive:
    network = Network.TESTNET

    def __init__(self, open_orders, *, reject=()):
        self.open_orders = open_orders
        self.reject = reject  # OrderTypes to reject
        self.placed = []
        self.canceled = []

    def get_marks(self):
        return {"BTC": 120.0}

    def get_candles(self, coin, *, interval="15m", lookback=48):
        return []

    def equity(self):
        return 10_000.0

    def get_positions(self):
        return []

    def get_open_orders(self):
        return list(self.open_orders)

    def place_order(self, order):
        self.placed.append(order)
        if order.order_type in self.reject:
            return OrderResult(accepted=False, status="error", message="rejected")
        return OrderResult(accepted=True, status="resting", order_id=str(100 + len(self.placed)),
                           filled_size=order.size, avg_price=None)

    def cancel(self, coin, oid):
        self.canceled.append(oid)
        return OrderResult(accepted=True, status="canceled")


def _resting(oid, order_type, price):
    return OpenOrder(coin="BTC", oid=oid, side=Side.SHORT, size=1.0, price=price,
                     order_type=order_type, reduce_only=True, is_trigger=True)


def test_live_extend_replaces_tp_trigger(tmp_path):
    state = StateStore(tmp_path / "s.db")
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    state.update_trade_sl(tid, 101.0)
    ex = FakeLive([_resting(1, "stop market", 101.0), _resting(2, "take profit market", 130.0)])

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("extend_tp", new_tp=138.0)))
    assert s.applied == 1
    (placed,) = ex.placed
    assert placed.order_type is OrderType.TAKE_PROFIT and placed.trigger_price == 138.0
    assert ex.canceled == [2]  # the old take-profit — never the stop
    assert state.open_trades()[0]["tp"] == 138.0


def test_live_extend_rejection_keeps_old_target(tmp_path):
    state = StateStore(tmp_path / "s.db")
    tid = state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    state.update_trade_sl(tid, 101.0)
    ex = FakeLive([_resting(2, "take profit market", 130.0)], reject=(OrderType.TAKE_PROFIT,))

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("extend_tp", new_tp=138.0)))
    assert s.applied == 0 and s.failed == 1
    assert ex.canceled == [] and state.open_trades()[0]["tp"] == 130.0


def test_live_close_cancels_surviving_triggers(tmp_path):
    state = StateStore(tmp_path / "s.db")
    state.open_trade("c1", "BTC", Side.LONG, 100.0, 90.0, 130.0, 1.0, 0.8, None, NOW)
    ex = FakeLive([_resting(1, "stop market", 90.0), _resting(2, "take profit market", 130.0)])

    s = manage_live(ex, state, _fast_caps(), tunable(), now=NOW, native_protected=True,
                    decide_fn=ScriptedManager(_payload("close")))
    assert s.applied == 1
    assert ex.placed[0].order_type is OrderType.MARKET and ex.placed[0].reduce_only
    assert sorted(ex.canceled) == [1, 2]  # the orphaned SL/TP pair
