"""Executor state — SQLite (PLAN.md §5, §12).

One network-scoped db (`state-<network>.db`) holding everything that must survive a
restart so a crashed executor never double-fires or loses its book:

  - `intake`            the candidate stream (monotonic `seq` = the high-water mark axis)
  - `meta`              key/value: the intake HWM, paper realized P&L
  - `idempotency`       fired keys → a restart re-running a candidate is a no-op
  - `decision_log`      full context + decision + gate + fill (audit + tuner data)
  - `trades`            opened positions and their resolved outcomes (the tuner's cohort source)
  - `deferred`          candidates the LLM said WAIT on, parked for a fresh re-check later
  - `paper_positions`   the persistent paper book

Pure persistence — fill math lives in the paper exchange, gate math in the gate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from hlcli.core.config import Caps
from hlcli.core.types import Candidate, Network, Side


@dataclass
class DeferredCandidate:
    """A parked WAIT candidate due for a re-check: the candidate plus its follow-up state."""

    candidate: Candidate
    next_check_at: float
    attempts_remaining: int

_SCHEMA = """
CREATE TABLE IF NOT EXISTS intake (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT UNIQUE NOT NULL,
    coin       TEXT NOT NULL,
    side       TEXT NOT NULL,
    entry      REAL NOT NULL,
    tp         REAL NOT NULL,
    sl         REAL NOT NULL,
    reasoning  TEXT NOT NULL DEFAULT '',
    news       TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    status     TEXT NOT NULL DEFAULT 'pending'
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS idempotency (key TEXT PRIMARY KEY, order_id TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS decision_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL, candidate_id TEXT NOT NULL,
    decision TEXT, gate TEXT, fill TEXT, context TEXT
);
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL, coin TEXT NOT NULL, side TEXT NOT NULL,
    entry REAL NOT NULL, sl REAL NOT NULL, tp REAL NOT NULL, size REAL NOT NULL,
    conviction REAL NOT NULL, regime TEXT, opened_at REAL NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',   -- open | won | lost | expired | aborted | closed
    exit_price REAL, realized REAL, r_multiple REAL, closed_at REAL,
    shadow INTEGER NOT NULL DEFAULT 0      -- 1 = hypothetical (shadow mode); no order behind it
);
CREATE TABLE IF NOT EXISTS deferred (
    id                 TEXT PRIMARY KEY,
    candidate          TEXT NOT NULL,      -- full Candidate JSON, to re-enrich/re-decide
    next_check_at      REAL NOT NULL,
    attempts_remaining INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS paper_positions (
    coin TEXT PRIMARY KEY, side TEXT NOT NULL, size REAL NOT NULL, entry_price REAL NOT NULL
);
"""

_HWM_KEY = "intake_hwm"
_REALIZED_KEY = "paper_realized"


class StateStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._migrate()
        self._conn.commit()

    def _migrate(self) -> None:
        """Additive column migrations for databases created by an older schema."""
        cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(trades)")}
        if "shadow" not in cols:
            self._conn.execute("ALTER TABLE trades ADD COLUMN shadow INTEGER NOT NULL DEFAULT 0")

    def close(self) -> None:
        self._conn.close()

    # --- intake stream ---

    def enqueue(self, candidate: Candidate) -> bool:
        """Add a candidate. Returns False if its id is already queued (dedupe)."""
        cur = self._conn.execute(
            "INSERT OR IGNORE INTO intake(id, coin, side, entry, tp, sl, reasoning, news, created_at)"
            " VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate.id, candidate.coin, candidate.side.value, candidate.entry, candidate.tp,
             candidate.sl, candidate.reasoning, candidate.news, candidate.created_at),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def pull_new(self, limit: int | None = None) -> list[tuple[int, Candidate]]:
        """Candidates past the high-water mark, oldest first — the unprocessed stream."""
        sql = "SELECT * FROM intake WHERE seq > ? ORDER BY seq"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        rows = self._conn.execute(sql, (self.get_hwm(),)).fetchall()
        return [(r["seq"], _to_candidate(r)) for r in rows]

    def get_hwm(self) -> int:
        return int(self._get_meta(_HWM_KEY, "0"))

    def advance_hwm(self, seq: int) -> None:
        if seq > self.get_hwm():
            self._set_meta(_HWM_KEY, str(seq))

    def set_status(self, seq: int, status: str) -> None:
        self._conn.execute("UPDATE intake SET status = ? WHERE seq = ?", (status, seq))
        self._conn.commit()

    # --- idempotency ---

    def already_fired(self, key: str) -> bool:
        return self._conn.execute("SELECT 1 FROM idempotency WHERE key = ?", (key,)).fetchone() is not None

    def record_fire(self, key: str, order_id: str | None, when: float) -> None:
        self._conn.execute(
            "INSERT OR IGNORE INTO idempotency(key, order_id, created_at) VALUES(?, ?, ?)",
            (key, order_id, when),
        )
        self._conn.commit()

    def release_fire(self, key: str) -> None:
        """Undo a recorded intent after a *definitive* reject (the order did not fill),
        so the key store reflects only orders that actually reached the book."""
        self._conn.execute("DELETE FROM idempotency WHERE key = ?", (key,))
        self._conn.commit()

    # --- decision log ---

    def log_decision(self, candidate_id: str, ts: float, *, decision=None, gate=None, fill=None, context=None) -> None:
        self._conn.execute(
            "INSERT INTO decision_log(ts, candidate_id, decision, gate, fill, context) VALUES(?, ?, ?, ?, ?, ?)",
            (ts, candidate_id, _dump(decision), _dump(gate), _dump(fill), _dump(context)),
        )
        self._conn.commit()

    def recent_decisions(self, limit: int = 20) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM decision_log ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    # --- trades (open → resolved; the tuner's cohort source) ---

    def open_trade(
        self, candidate_id: str, coin: str, side: Side, entry: float, sl: float, tp: float,
        size: float, conviction: float, regime: str | None, opened_at: float,
        *, shadow: bool = False,
    ) -> int:
        cur = self._conn.execute(
            "INSERT INTO trades(candidate_id, coin, side, entry, sl, tp, size, conviction,"
            " regime, opened_at, shadow) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (candidate_id, coin, side.value, entry, sl, tp, size, conviction, regime,
             opened_at, int(shadow)),
        )
        self._conn.commit()
        return cur.lastrowid

    def open_trades(self, *, shadow: bool | None = None) -> list[dict]:
        """Open ledger rows; `shadow` filters to hypothetical (True) or real (False)."""
        sql = "SELECT * FROM trades WHERE status = 'open'"
        if shadow is not None:
            sql += f" AND shadow = {int(shadow)}"
        return [dict(r) for r in self._conn.execute(sql + " ORDER BY id").fetchall()]

    def resolve_trade(
        self, trade_id: int, status: str, exit_price: float, realized: float,
        r_multiple: float, closed_at: float,
    ) -> None:
        self._conn.execute(
            "UPDATE trades SET status=?, exit_price=?, realized=?, r_multiple=?, closed_at=?"
            " WHERE id=?",
            (status, exit_price, realized, r_multiple, closed_at, trade_id),
        )
        self._conn.commit()

    def resolved_trades(self, limit: int | None = None) -> list[dict]:
        """Resolved rows, newest-closed first — a `limit` means "the most recent N"."""
        sql = "SELECT * FROM trades WHERE status != 'open' ORDER BY closed_at DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._conn.execute(sql).fetchall()]

    # --- deferred follow-ups (LLM said WAIT; re-checked later with fresh data) ---

    def defer_candidate(self, candidate: Candidate, next_check_at: float, attempts_remaining: int) -> None:
        """Park (or re-park) a candidate for a later re-check. Keyed by candidate id."""
        self._conn.execute(
            "INSERT INTO deferred(id, candidate, next_check_at, attempts_remaining) VALUES(?, ?, ?, ?)"
            " ON CONFLICT(id) DO UPDATE SET next_check_at=excluded.next_check_at,"
            " attempts_remaining=excluded.attempts_remaining, candidate=excluded.candidate",
            (candidate.id, candidate.model_dump_json(), next_check_at, attempts_remaining),
        )
        self._conn.commit()

    def due_deferred(self, now: float) -> list[DeferredCandidate]:
        """Parked candidates whose re-check time has arrived, oldest-due first."""
        rows = self._conn.execute(
            "SELECT * FROM deferred WHERE next_check_at <= ? ORDER BY next_check_at", (now,)
        ).fetchall()
        return [
            DeferredCandidate(
                candidate=Candidate.model_validate_json(r["candidate"]),
                next_check_at=r["next_check_at"],
                attempts_remaining=r["attempts_remaining"],
            )
            for r in rows
        ]

    def drop_deferred(self, candidate_id: str) -> None:
        """Remove a parked candidate — it reached a terminal verdict (fired/skipped/expired)."""
        self._conn.execute("DELETE FROM deferred WHERE id = ?", (candidate_id,))
        self._conn.commit()

    def deferred_count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM deferred").fetchone()[0]

    # --- paper book ---

    def paper_positions(self) -> dict[str, dict]:
        return {
            r["coin"]: {"side": Side(r["side"]), "size": r["size"], "entry_price": r["entry_price"]}
            for r in self._conn.execute("SELECT * FROM paper_positions").fetchall()
        }

    def upsert_paper_position(self, coin: str, side: Side, size: float, entry_price: float) -> None:
        self._conn.execute(
            "INSERT INTO paper_positions(coin, side, size, entry_price) VALUES(?, ?, ?, ?)"
            " ON CONFLICT(coin) DO UPDATE SET side=excluded.side, size=excluded.size, entry_price=excluded.entry_price",
            (coin, side.value, size, entry_price),
        )
        self._conn.commit()

    def delete_paper_position(self, coin: str) -> None:
        self._conn.execute("DELETE FROM paper_positions WHERE coin = ?", (coin,))
        self._conn.commit()

    def paper_realized(self) -> float:
        return float(self._get_meta(_REALIZED_KEY, "0"))

    def add_paper_realized(self, delta: float) -> None:
        self._set_meta(_REALIZED_KEY, str(self.paper_realized() + delta))

    # --- breaker / generic meta (used by safety.breaker) ---

    def breaker_tripped(self) -> bool:
        return self._get_meta("breaker", "0") == "1"

    def set_breaker(self, on: bool) -> None:
        self._set_meta("breaker", "1" if on else "0")

    def meta_get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def meta_set(self, key: str, value: str) -> None:
        self._set_meta(key, value)

    # --- meta helpers ---

    def _get_meta(self, key: str, default: str) -> str:
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else default

    def _set_meta(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()


def _to_candidate(row: sqlite3.Row) -> Candidate:
    return Candidate(
        id=row["id"], coin=row["coin"], side=Side(row["side"]), entry=row["entry"],
        tp=row["tp"], sl=row["sl"], reasoning=row["reasoning"], news=row["news"],
        created_at=row["created_at"],
    )


def _dump(value) -> str | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    return json.dumps(value, default=str)


def open_state(caps: Caps, network: Network) -> StateStore:
    return StateStore(caps.data_dir / f"state-{network.value}.db")
