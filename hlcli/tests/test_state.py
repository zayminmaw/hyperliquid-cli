"""Intake stream, high-water mark, idempotency, decision log."""

import sqlite3

import pytest

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


def test_producer_verdict_round_trips_through_the_intake_stream(tmp_path):
    s = _store(tmp_path)
    c = Candidate(id="v", coin="BTC", side=Side.LONG, entry=100, tp=120, sl=90,
                  source_direction="WAIT", source_confidence=0.42, created_at=1.0)
    s.enqueue(c)
    [(_, pulled)] = s.pull_new()
    assert pulled.source_direction == "WAIT" and pulled.source_confidence == 0.42
    # a verdict-less candidate reads back as None, not a fabricated default
    s.enqueue(_cand("plain"))
    plain = s.intake_candidate("plain")
    assert plain.source_direction is None and plain.source_confidence is None


def test_read_only_store_reads_but_never_writes(tmp_path):
    # `exec report --compare` opens the other book read-only: reads round-trip, but no
    # write, schema-create, or additive migration may touch a book we only mean to compare.
    path = tmp_path / "state.db"
    StateStore(path).close()  # a normal, initialized book

    ro = StateStore(path, read_only=True)
    try:
        assert ro.resolved_trades() == []  # reads work
        with pytest.raises(sqlite3.OperationalError):
            ro.enqueue(_cand("x"))  # any write is refused by the read-only connection
    finally:
        ro.close()


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
