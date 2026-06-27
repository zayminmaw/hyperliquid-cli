"""Intake stream, high-water mark, idempotency, decision log."""

from hlcli.core.types import Candidate, Side
from hlcli.state.store import StateStore


def _store(tmp_path) -> StateStore:
    return StateStore(tmp_path / "state.db")


def _cand(id="a", coin="BTC") -> Candidate:
    return Candidate(id=id, coin=coin, side=Side.LONG, entry=100, tp=120, sl=90, created_at=1.0)


def test_enqueue_dedupes_by_id(tmp_path):
    s = _store(tmp_path)
    assert s.enqueue(_cand("a")) is True
    assert s.enqueue(_cand("a")) is False  # same id ignored


def test_pull_new_respects_hwm(tmp_path):
    s = _store(tmp_path)
    s.enqueue(_cand("a"))
    s.enqueue(_cand("b"))
    batch = s.pull_new()
    assert [c.id for _, c in batch] == ["a", "b"]

    first_seq = batch[0][0]
    s.advance_hwm(first_seq)
    assert [c.id for _, c in s.pull_new()] == ["b"]  # 'a' is behind the HWM now


def test_idempotency(tmp_path):
    s = _store(tmp_path)
    assert not s.already_fired("a")
    s.record_fire("a", "oid-1", 1.0)
    assert s.already_fired("a")


def test_decision_log_roundtrip(tmp_path):
    s = _store(tmp_path)
    s.log_decision("a", 1.0, decision={"action": "act"}, gate={"approved": True}, context={"equity": 100})
    rows = s.recent_decisions()
    assert rows[0]["candidate_id"] == "a"
