"""A durable local queue between the trading process and the database.

The rule the whole module is built on: capture never blocks trading. A
freqtrade callback calls `enqueue()`, which is one INSERT into a local SQLite
file and returns in well under a millisecond, and a background writer ships
the rows to Supabase on its own schedule. If Supabase is slow, down, or
restricted, the rows wait here; if this process is restarted, the file is
still here.

`enqueue()` never raises. If SQLite itself fails -- disk full, a corrupt file
-- a circuit breaker drops events for a minute and counts them, because a
learning record is worth a great deal less than an uninterrupted trading
loop. The count is published, so a broken outbox is visible rather than
silent.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass

#: Backoff between attempts to ship a row, by attempt number (1-based),
#: capped at the last entry.
BACKOFF_SECONDS = (1, 2, 4, 8, 16, 32, 60)


@dataclass(frozen=True)
class Queued:
    seq: int
    kind: str
    idempotency_key: str
    payload: str
    attempts: int


class SqliteOutbox:
    SCHEMA = """
    create table if not exists outbox (
      seq integer primary key autoincrement,
      kind text not null,
      idempotency_key text not null unique,
      payload text not null,
      created_at real not null,
      attempts integer not null default 0,
      state text not null default 'pending',
      next_at real not null default 0,
      last_error text,
      sent_at real
    );
    create index if not exists outbox_pending on outbox (state, next_at, seq);
    create table if not exists correlations (
      ft_trade_id integer primary key,
      position_id text,
      origin_decision_id text,
      updated_at real not null
    );
    create table if not exists meta (k text primary key, v text);
    """

    def __init__(self, path: str, *, breaker_failures: int = 3, breaker_slow_ms: float = 250.0,
                 breaker_open_seconds: float = 60.0) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._breaker_failures = breaker_failures
        self._breaker_slow_ms = breaker_slow_ms
        self._breaker_open_seconds = breaker_open_seconds
        self._failures = 0
        self._open_until = 0.0
        self.dropped_total = 0
        self.last_error: str | None = None
        self.breaker_trips = 0

    # -- connection --------------------------------------------------------
    def _connection(self) -> sqlite3.Connection:
        if self._conn is None:
            directory = os.path.dirname(os.path.abspath(self.path))
            os.makedirs(directory, exist_ok=True)
            conn = sqlite3.connect(self.path, timeout=5.0, check_same_thread=False,
                                   isolation_level=None)
            conn.execute("pragma journal_mode=wal")
            conn.execute("pragma synchronous=normal")
            conn.execute("pragma busy_timeout=5000")
            conn.executescript(self.SCHEMA)
            self._conn = conn
        return self._conn

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                finally:
                    self._conn = None

    # -- the circuit breaker -------------------------------------------------
    def breaker_open(self, now: float | None = None) -> bool:
        return (time.monotonic() if now is None else now) < self._open_until

    def _trip(self, reason: str) -> None:
        self._open_until = time.monotonic() + self._breaker_open_seconds
        self._failures = 0
        self.breaker_trips += 1
        self.last_error = reason
        # Start over next time: whatever state the connection is in, it is
        # not one to trust.
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:  # noqa: BLE001
                pass
            self._conn = None

    # -- writing -----------------------------------------------------------
    def enqueue(self, kind: str, idempotency_key: str, payload: dict) -> bool:
        """Queue one record. True if it was new, False if a duplicate or dropped.

        Never raises: this runs inside freqtrade's callbacks.
        """
        with self._lock:
            if self.breaker_open():
                self.dropped_total += 1
                return False
            started = time.monotonic()
            try:
                body = json.dumps(payload, default=str, separators=(",", ":"))
                cursor = self._connection().execute(
                    "insert or ignore into outbox (kind, idempotency_key, payload, created_at) "
                    "values (?, ?, ?, ?)",
                    (kind, idempotency_key, body, time.time()),
                )
                inserted = cursor.rowcount == 1
                self._failures = 0
                elapsed_ms = (time.monotonic() - started) * 1000
                if elapsed_ms > self._breaker_slow_ms:
                    self._trip(f"outbox insert took {elapsed_ms:.0f}ms")
                return inserted
            except Exception as exc:  # noqa: BLE001 - the trading loop is above us
                self._failures += 1
                self.dropped_total += 1
                self.last_error = str(exc)[:300]
                if self._failures >= self._breaker_failures:
                    self._trip(str(exc)[:300])
                return False

    # -- shipping ----------------------------------------------------------
    def claim(self, limit: int = 200, now: float | None = None) -> list[Queued]:
        """The next rows to ship, in order, up to the first one not yet due.

        Strictly in order: a row waiting out a backoff holds everything behind
        it. That is what keeps an event from ever reaching the database before
        its decision. A quarantined row is out of the queue, so a row that will
        never go through cannot hold it forever.
        """
        now = time.time() if now is None else now
        with self._lock:
            rows = self._connection().execute(
                "select seq, kind, idempotency_key, payload, attempts, next_at from outbox "
                "where state = 'pending' order by seq limit ?",
                (int(limit),),
            ).fetchall()
        due: list[Queued] = []
        for seq, kind, key, payload, attempts, next_at in rows:
            if next_at > now:
                break
            due.append(Queued(seq, kind, key, payload, attempts))
        return due

    def mark_sent(self, seqs: list[int]) -> None:
        if not seqs:
            return
        with self._lock:
            marks = ",".join("?" for _ in seqs)
            self._connection().execute(
                f"update outbox set state = 'sent', sent_at = ? where seq in ({marks})",
                (time.time(), *seqs),
            )

    def mark_failed(self, seq: int, error: str, *, max_attempts: int = 10,
                    now: float | None = None) -> str:
        """Schedule a retry with backoff, or quarantine after max_attempts.
        Returns the row's new state."""
        now = time.time() if now is None else now
        with self._lock:
            conn = self._connection()
            row = conn.execute("select attempts from outbox where seq = ?", (seq,)).fetchone()
            attempts = (row[0] if row else 0) + 1
            if attempts >= max_attempts:
                conn.execute(
                    "update outbox set state = 'quarantined', attempts = ?, last_error = ? where seq = ?",
                    (attempts, error[:500], seq),
                )
                return "quarantined"
            wait = BACKOFF_SECONDS[min(attempts, len(BACKOFF_SECONDS)) - 1]
            conn.execute(
                "update outbox set attempts = ?, next_at = ?, last_error = ? where seq = ?",
                (attempts, now + wait, error[:500], seq),
            )
            return "pending"

    def prune_sent(self, older_than_seconds: float = 6 * 3600) -> int:
        with self._lock:
            cursor = self._connection().execute(
                "delete from outbox where state = 'sent' and sent_at < ?",
                (time.time() - older_than_seconds,),
            )
            return cursor.rowcount

    # -- correlation memory ------------------------------------------------
    def remember_correlation(self, ft_trade_id: int, position_id: str | None,
                             origin_decision_id: str | None) -> None:
        with self._lock:
            try:
                self._connection().execute(
                    "insert into correlations (ft_trade_id, position_id, origin_decision_id, updated_at) "
                    "values (?, ?, ?, ?) on conflict (ft_trade_id) do update set "
                    "position_id = coalesce(excluded.position_id, correlations.position_id), "
                    "origin_decision_id = coalesce(excluded.origin_decision_id, correlations.origin_decision_id), "
                    "updated_at = excluded.updated_at",
                    (int(ft_trade_id), position_id, origin_decision_id, time.time()),
                )
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)[:300]

    def correlation(self, ft_trade_id: int) -> tuple[str | None, str | None] | None:
        with self._lock:
            try:
                row = self._connection().execute(
                    "select position_id, origin_decision_id from correlations where ft_trade_id = ?",
                    (int(ft_trade_id),),
                ).fetchone()
            except Exception:  # noqa: BLE001
                return None
        return (row[0], row[1]) if row else None

    # -- observability -----------------------------------------------------
    def stats(self) -> dict:
        try:
            with self._lock:
                conn = self._connection()
                pending, oldest = conn.execute(
                    "select count(*), min(created_at) from outbox where state = 'pending'"
                ).fetchone()
                quarantined = conn.execute(
                    "select count(*) from outbox where state = 'quarantined'"
                ).fetchone()[0]
                sent = conn.execute("select count(*) from outbox where state = 'sent'").fetchone()[0]
        except Exception as exc:  # noqa: BLE001
            return {"outbox_pending": None, "outbox_oldest_age_seconds": None,
                    "outbox_quarantined": None, "outbox_sent": None,
                    "dropped_total": self.dropped_total, "breaker_open": self.breaker_open(),
                    "breaker_trips": self.breaker_trips, "last_error": str(exc)[:300]}
        return {
            "outbox_pending": pending,
            "outbox_oldest_age_seconds": int(time.time() - oldest) if oldest else 0,
            "outbox_quarantined": quarantined,
            "outbox_sent": sent,
            "dropped_total": self.dropped_total,
            "breaker_open": self.breaker_open(),
            "breaker_trips": self.breaker_trips,
            "last_error": self.last_error,
        }
