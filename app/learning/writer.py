"""The background writer: ships the outbox to Supabase, in order, forever.

Rows leave in `seq` order and consecutive rows of the same kind go as one
insert. A decision is always queued before the events that reference it, and
the outbox hands rows out strictly in order -- a row that is waiting to be
retried holds everything behind it -- so an event can never reach the
database before its decision.

Two kinds of failure, treated differently because they mean different things:

  The database could not be reached, or refused everything: a connection
  error, a timeout, a 5xx, a bad key, a missing table. Nothing is wrong with
  the rows. The writer pauses, doubling its wait up to a minute, and then
  tries the same rows again. No row is charged an attempt for an outage, so
  an outage of any length quarantines nothing.

  The database refused a row -- 400, 409, 422: a bad enum value, a broken
  reference. The rows of that batch are sent one at a time to find the
  culprit; the good ones go through, the bad one is charged an attempt and
  backed off, and after ten attempts it is quarantined -- counted, kept,
  never dropped -- so the queue behind it can move.

Inserts use `resolution=ignore-duplicates`, so replaying a row the database
already has is free and never an update: the evidence tables are append-only
and refuse updates anyway.
"""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone

from app.learning.outbox import Queued, SqliteOutbox

#: Where each kind of record goes, and the unique key duplicates collide on.
TABLES = {
    "decision": ("trading_decisions", "idempotency_key"),
    "event": ("trading_events", "idempotency_key"),
}

#: HTTP statuses that say "this row is wrong", as opposed to "I am not well".
#: 401/403/404 and 5xx are the database's problem, or the deployment's, and
#: charging a row for them would quarantine perfectly good records.
ROW_FAULT_STATUSES = frozenset({400, 409, 413, 422})

#: How long the writer waits after an outage: doubling from the first value,
#: capped at the second.
OUTAGE_BACKOFF_MIN = 1.0
OUTAGE_BACKOFF_MAX = 60.0


def _groups(rows: list[Queued]) -> list[tuple[str, list[Queued]]]:
    """Consecutive runs of the same kind, in order."""
    grouped: list[tuple[str, list[Queued]]] = []
    for row in rows:
        if grouped and grouped[-1][0] == row.kind:
            grouped[-1][1].append(row)
        else:
            grouped.append((row.kind, [row]))
    return grouped


def is_the_rows_fault(exc: BaseException) -> bool:
    """A refusal of the row itself, rather than a failure to talk to the database."""
    status = getattr(exc, "status", None)
    return isinstance(status, int) and status in ROW_FAULT_STATUSES


class LearningWriter(threading.Thread):
    def __init__(self, outbox: SqliteOutbox, client, *, interval: float = 2.0, batch: int = 200,
                 max_attempts: int = 10, prune_after: float = 6 * 3600) -> None:
        super().__init__(daemon=True, name="learning-writer")
        self.outbox = outbox
        self.client = client
        self.interval = interval
        self.batch = batch
        self.max_attempts = max_attempts
        self.prune_after = prune_after
        self._stopping = threading.Event()  # not _stop: Thread has one
        self._drain_lock = threading.Lock()
        self._last_prune = time.monotonic()
        self._outage_backoff = 0.0
        self._paused_until = 0.0
        self.written_total = 0
        self.failed_total = 0
        self.retried_total = 0
        self.quarantined_total = 0
        self.outages_total = 0
        self.last_success_at: str | None = None
        self.last_error: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def run(self) -> None:
        while not self._stopping.wait(self.interval):
            try:
                self.drain()
            except Exception as exc:  # noqa: BLE001 - the writer never dies
                self.last_error = str(exc)[:500]
            if time.monotonic() - self._last_prune > 600:
                try:
                    self.outbox.prune_sent(self.prune_after)
                except Exception:  # noqa: BLE001
                    pass
                self._last_prune = time.monotonic()

    def stop(self) -> None:
        self._stopping.set()

    # -- shipping ----------------------------------------------------------
    def paused_for(self) -> float:
        """Seconds until the writer will try the database again; 0 when it will now."""
        return max(0.0, self._paused_until - time.monotonic())

    def drain(self, limit: int | None = None) -> int:
        """Ship what is due. Returns how many rows were written."""
        with self._drain_lock:
            if time.monotonic() < self._paused_until:
                return 0
            rows = self.outbox.claim(limit or self.batch)
            sent = 0
            for kind, group in _groups(rows):
                if kind not in TABLES:
                    for row in group:
                        self._set_aside(row, f"unknown kind {kind!r}")
                    continue
                table, conflict = TABLES[kind]
                shipped = self._ship(table, conflict, group)
                sent += shipped
                if shipped < len(group):
                    break  # what is behind the failure waits for it
            if sent:
                self._outage_backoff = 0.0
            return sent

    def _ship(self, table: str, conflict: str, group: list[Queued]) -> int:
        payloads = []
        for index, row in enumerate(group):
            try:
                payloads.append(json.loads(row.payload))
            except ValueError as exc:
                # Everything queued ahead of it still goes; it is set aside; what
                # is behind it waits for the next pass.
                sent = self._ship(table, conflict, group[:index]) if index else 0
                if sent == index:
                    self._set_aside(row, f"payload will not decode: {exc}")
                return sent
        try:
            self.client.insert_new_only(table, payloads, on_conflict=conflict)
        except Exception as exc:  # noqa: BLE001 - sorted out below
            if not is_the_rows_fault(exc):
                self._outage(table, exc)
                return 0
            return self._isolate(table, conflict, group, payloads, exc)
        self._sent(group)
        return len(group)

    def _isolate(self, table: str, conflict: str, group: list[Queued], payloads: list,
                 exc: BaseException) -> int:
        """The batch was refused. Send its rows one at a time to find which."""
        if len(group) == 1:
            self._refused(table, group[0], exc)
            return 0
        sent = 0
        for row, payload in zip(group, payloads):
            try:
                self.client.insert_new_only(table, [payload], on_conflict=conflict)
            except Exception as one:  # noqa: BLE001
                if is_the_rows_fault(one):
                    self._refused(table, row, one)
                else:
                    self._outage(table, one)
                return sent
            self._sent([row])
            sent += 1
        return sent

    def _sent(self, rows: list[Queued]) -> None:
        self.outbox.mark_sent([row.seq for row in rows])
        self.written_total += len(rows)
        self.last_success_at = datetime.now(timezone.utc).isoformat()

    def _refused(self, table: str, row: Queued, exc: BaseException) -> None:
        self.failed_total += 1
        self.last_error = f"{table}: {str(exc)[:400]}"
        state = self.outbox.mark_failed(row.seq, str(exc), max_attempts=self.max_attempts)
        if state == "quarantined":
            self.quarantined_total += 1
        else:
            self.retried_total += 1

    def _set_aside(self, row: Queued, reason: str) -> None:
        """Quarantine at once: a row that can never be sent must not hold the queue."""
        self.outbox.mark_failed(row.seq, reason, max_attempts=1)
        self.failed_total += 1
        self.quarantined_total += 1
        self.last_error = reason[:500]

    def _outage(self, table: str, exc: BaseException) -> None:
        self.outages_total += 1
        self.last_error = f"{table}: {str(exc)[:400]}"
        self._outage_backoff = min(OUTAGE_BACKOFF_MAX,
                                   max(OUTAGE_BACKOFF_MIN, self._outage_backoff * 2))
        self._paused_until = time.monotonic() + self._outage_backoff

    def flush(self, timeout: float = 10.0) -> bool:
        """Drain until nothing is pending or the time is up. True when empty.

        Called on the way out, so any pause is waived: one more try is always
        worth making before the process goes away.
        """
        deadline = time.monotonic() + timeout
        self._paused_until = 0.0
        while True:
            pending = self.outbox.stats().get("outbox_pending") or 0
            if pending == 0:
                return True
            if time.monotonic() >= deadline:
                return False
            if self.drain() == 0:
                time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))

    # -- observability -----------------------------------------------------
    def health(self) -> dict:
        return {
            **self.outbox.stats(),
            "written_total": self.written_total,
            "failed_total": self.failed_total,
            "retried_total": self.retried_total,
            "quarantined_total": self.quarantined_total,
            "outages_total": self.outages_total,
            "paused_for_seconds": round(self.paused_for(), 1),
            "last_success_at": self.last_success_at,
            "writer_error": self.last_error,
            "writer_alive": self.is_alive(),
        }
