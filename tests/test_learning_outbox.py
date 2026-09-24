"""The outbox: the queue between the trading loop and the database.

The property that matters most is the negative one: enqueue() never raises
and never waits, whatever state the file or the disk is in, because it runs
inside freqtrade's callbacks. The rest is that nothing queued is lost,
nothing queued twice is stored twice, and rows come out in the order they
went in -- with a row that is backing off holding everything behind it.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

import pytest

import app.learning.outbox as outbox_module
from app.learning.outbox import BACKOFF_SECONDS, SqliteOutbox


class FakeClock:
    def __init__(self, start: float = 1_000.0) -> None:
        self.now = start

    def time(self) -> float:
        return self.now

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = FakeClock()
    monkeypatch.setattr(outbox_module, "time", fake)
    return fake


@pytest.fixture
def box(tmp_path):
    outbox = SqliteOutbox(str(tmp_path / "outbox.sqlite"))
    yield outbox
    outbox.close()


def test_a_queued_record_survives_the_process_that_queued_it(tmp_path):
    path = str(tmp_path / "outbox.sqlite")
    first = SqliteOutbox(path)
    assert first.enqueue("decision", "k1", {"symbol": "TRX/USDT"}) is True
    first.close()

    second = SqliteOutbox(path)
    try:
        claimed = second.claim()
        assert [(row.kind, row.idempotency_key) for row in claimed] == [("decision", "k1")]
        assert json.loads(claimed[0].payload) == {"symbol": "TRX/USDT"}
    finally:
        second.close()


def test_the_same_record_queued_twice_is_stored_once(box):
    assert box.enqueue("event", "e1", {"n": 1}) is True
    assert box.enqueue("event", "e1", {"n": 2}) is False
    rows = box.claim()
    assert len(rows) == 1
    assert json.loads(rows[0].payload) == {"n": 1}


def test_records_come_out_in_the_order_they_went_in(box):
    for i in range(5):
        box.enqueue("event", f"e{i}", {"i": i})
    rows = box.claim()
    assert [json.loads(row.payload)["i"] for row in rows] == [0, 1, 2, 3, 4]
    assert [row.seq for row in rows] == sorted(row.seq for row in rows)


def test_a_payload_with_a_datetime_in_it_is_still_queued(box):
    assert box.enqueue("event", "e", {"when": datetime(2026, 9, 17, tzinfo=timezone.utc)}) is True
    assert "2026-09-17" in box.claim()[0].payload


def test_a_row_waiting_to_be_retried_holds_everything_behind_it(box, clock):
    box.enqueue("decision", "d", {})
    box.enqueue("event", "e1", {})
    box.enqueue("event", "e2", {})
    decision = box.claim()[0]
    assert box.mark_failed(decision.seq, "boom") == "pending"
    assert box.claim() == []  # nothing jumps the queue
    clock.advance(BACKOFF_SECONDS[0])
    assert [row.idempotency_key for row in box.claim()] == ["d", "e1", "e2"]


def test_retries_back_off_further_each_time(box, clock):
    box.enqueue("event", "e", {})
    seq = box.claim()[0].seq
    for attempt, wait in enumerate(BACKOFF_SECONDS[:4], start=1):
        assert box.mark_failed(seq, "boom") == "pending"
        clock.advance(wait - 0.5)
        assert box.claim() == []
        clock.advance(0.5)
        assert [row.attempts for row in box.claim()] == [attempt]


def test_a_row_that_keeps_failing_is_quarantined_not_dropped(box, clock):
    box.enqueue("event", "bad", {})
    box.enqueue("event", "good", {})
    seq = box.claim()[0].seq
    states = []
    for _ in range(3):
        states.append(box.mark_failed(seq, "invalid", max_attempts=3))
        clock.advance(120)
    assert states == ["pending", "pending", "quarantined"]
    assert [row.idempotency_key for row in box.claim()] == ["good"]
    stats = box.stats()
    assert stats["outbox_quarantined"] == 1
    assert stats["outbox_pending"] == 1


def test_sent_rows_leave_the_queue_and_are_pruned_later(box, clock):
    box.enqueue("event", "e", {})
    seq = box.claim()[0].seq
    box.mark_sent([seq])
    assert box.claim() == []
    assert box.stats()["outbox_sent"] == 1
    clock.advance(7 * 3600)
    assert box.prune_sent(6 * 3600) == 1
    assert box.stats()["outbox_sent"] == 0


def test_a_broken_file_never_raises_and_counts_what_it_lost():
    doomed = SqliteOutbox("/dev/null/not/a/directory/outbox.sqlite")
    assert doomed.enqueue("event", "e", {}) is False
    assert doomed.dropped_total == 1
    assert doomed.last_error
    assert doomed.stats()["outbox_pending"] is None
    assert doomed.stats()["dropped_total"] == 1


def test_three_failures_open_the_breaker_for_a_minute(box, clock, monkeypatch):
    calls = []

    def broken():
        calls.append(1)
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(box, "_connection", broken)
    for i in range(3):
        assert box.enqueue("event", f"e{i}", {}) is False
    assert box.breaker_open() is True
    assert box.breaker_trips == 1

    # While it is open, nothing touches SQLite: the loss is counted, not retried.
    seen = len(calls)
    assert box.enqueue("event", "late", {}) is False
    assert len(calls) == seen
    assert box.dropped_total == 4

    monkeypatch.delattr(box, "_connection")  # back to the real connection
    clock.advance(61)
    assert box.breaker_open() is False
    assert box.enqueue("event", "after", {}) is True
    assert [row.idempotency_key for row in box.claim()] == ["after"]


def test_a_slow_insert_opens_the_breaker(tmp_path, clock, monkeypatch):
    box = SqliteOutbox(str(tmp_path / "slow.sqlite"), breaker_slow_ms=250)
    real_connection = box._connection

    class Slow:
        """A connection whose every statement takes 300 ms on the fake clock."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, *args):
            clock.advance(0.3)
            return self._conn.execute(*args)

    monkeypatch.setattr(box, "_connection", lambda: Slow(real_connection()))
    try:
        assert box.enqueue("event", "e", {}) is True  # the slow write itself lands
        assert box.breaker_open() is True
        assert box.breaker_trips == 1
        assert box.last_error.startswith("outbox insert took")
        assert box.enqueue("event", "e2", {}) is False
        assert box.dropped_total == 1
    finally:
        box.close()


def test_correlations_are_remembered_and_merged(box):
    box.remember_correlation(7, "pos-1", None)
    box.remember_correlation(7, None, "dec-1")
    assert box.correlation(7) == ("pos-1", "dec-1")
    assert box.correlation(8) is None


def test_stats_describe_the_queue(box, clock):
    box.enqueue("event", "e1", {})
    clock.advance(30)
    box.enqueue("event", "e2", {})
    stats = box.stats()
    assert stats["outbox_pending"] == 2
    assert stats["outbox_oldest_age_seconds"] == 30
    assert stats["outbox_quarantined"] == 0
    assert stats["dropped_total"] == 0
    assert stats["breaker_open"] is False
