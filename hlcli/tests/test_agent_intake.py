"""Intake-directory watcher (PLAN.md §15.1): parse → enqueue → archive, dedupe on
re-drop, quarantine + alert on bad content, settle window for mid-write files."""

from __future__ import annotations

import io
import json
from pathlib import Path

from hlcli.agent.intake_watch import _SETTLE_SECONDS, poll
from hlcli.safety.alerts import Alerter
from hlcli.state.store import StateStore

BATCH = [
    {"coin": "BTC", "entry": 100.0, "tp": 110.0, "sl": 95.0, "reasoning": "breakout"},
    {"coin": "ETH", "entry": 1500.0, "tp": 1600.0, "sl": 1450.0},
]


def settled(now: float) -> float:
    return now + _SETTLE_SECONDS + 1  # poll time at which files written "now" are settled


def drop(directory: Path, payload, name: str = "batch.json") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_text(json.dumps(payload))
    return path


def make_env(tmp_path: Path):
    state = StateStore(tmp_path / "state.db")
    stream = io.StringIO()
    return tmp_path / "intake", state, Alerter(stream=stream), stream


def test_batch_file_enqueues_and_archives(tmp_path):
    directory, state, alerter, _ = make_env(tmp_path)
    path = drop(directory, BATCH)

    result = poll(directory, state, alerter, now=settled(path.stat().st_mtime))

    assert (result.files, result.enqueued, result.duplicates, result.failed) == (1, 2, 0, 0)
    assert not path.exists()
    assert (directory / "processed" / "batch.json").exists()
    assert len(state.pull_new()) == 2


def test_single_object_file_is_a_batch_of_one(tmp_path):
    directory, state, alerter, _ = make_env(tmp_path)
    path = drop(directory, BATCH[0])
    result = poll(directory, state, alerter, now=settled(path.stat().st_mtime))
    assert result.enqueued == 1


def test_redrop_dedupes_and_archives_under_a_fresh_name(tmp_path):
    directory, state, alerter, _ = make_env(tmp_path)
    path = drop(directory, BATCH)
    poll(directory, state, alerter, now=settled(path.stat().st_mtime))

    path = drop(directory, BATCH)  # same content, same filename
    result = poll(directory, state, alerter, now=settled(path.stat().st_mtime))

    assert (result.enqueued, result.duplicates) == (0, 2)
    # both raw batches survive for the audit trail
    assert len(list((directory / "processed").iterdir())) == 2


def test_unparseable_file_is_quarantined_and_alerted(tmp_path):
    directory, state, alerter, stream = make_env(tmp_path)
    directory.mkdir(parents=True)
    path = directory / "bad.json"
    path.write_text("{not json")

    result = poll(directory, state, alerter, now=settled(path.stat().st_mtime))

    assert (result.failed, result.enqueued) == (1, 0)
    assert (directory / "failed" / "bad.json").exists()
    assert "intake_file_failed" in stream.getvalue()


def test_missing_required_field_is_quarantined(tmp_path):
    directory, state, alerter, _ = make_env(tmp_path)
    path = drop(directory, [{"entry": 100.0, "tp": 110.0, "sl": 95.0}])  # no coin
    result = poll(directory, state, alerter, now=settled(path.stat().st_mtime))
    assert result.failed == 1
    assert (directory / "failed" / "batch.json").exists()


def test_fresh_file_waits_for_the_settle_window(tmp_path):
    directory, state, alerter, _ = make_env(tmp_path)
    path = drop(directory, BATCH)
    mtime = path.stat().st_mtime

    early = poll(directory, state, alerter, now=mtime + 0.5)
    assert (early.files, early.enqueued) == (0, 0)
    assert path.exists()

    late = poll(directory, state, alerter, now=settled(mtime))
    assert late.enqueued == 2


def test_missing_directory_is_a_quiet_noop(tmp_path):
    _, state, alerter, _ = make_env(tmp_path)
    result = poll(tmp_path / "nope", state, alerter)
    assert result.files == 0
