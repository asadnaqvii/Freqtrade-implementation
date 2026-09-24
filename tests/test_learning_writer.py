"""The writer: ships the outbox to the database in order, and survives it.

A fake client stands in for Supabase. It records every insert, refuses the
rows it is told to refuse, goes "down" on request, and raises on any verb the
writer must never use -- the evidence tables are append-only, so an upsert
or an update from here is a bug, not a strategy.
"""

from __future__ import annotations

import importlib
import pkgutil
import time as real_time

import pytest

import app.learning as learning
import app.learning.outbox as outbox_module
import app.learning.writer as writer_module
from app.learning.outbox import SqliteOutbox
from app.learning.writer import (
    OUTAGE_BACKOFF_MIN,
    LearningWriter,
    is_the_rows_fault,
)


class Refused(Exception):
    """What SupabaseError looks like to the writer: an exception with a status."""

    def __init__(self, status: int, body: str = "refused") -> None:
        super().__init__(f"supabase returned {status}: {body}")
        self.status = status


class Unreachable(Exception):
    """A transport failure: no status at all, like an httpx error."""


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, list[dict], str]] = []
        self.down = False
        self.refuse = lambda payload: False

    def insert_new_only(self, table, rows, *, on_conflict):
        if self.down:
            raise Unreachable("connection refused")
        bad = [row for row in rows if self.refuse(row)]
        if bad:
            raise Refused(400, f"invalid input: {bad[0]}")
        self.calls.append((table, [dict(row) for row in rows], on_conflict))

    def _never(self, *args, **kwargs):
        raise AssertionError("the writer must only ever insert: the evidence tables are append-only")

    upsert = insert = update = delete = rpc = _never


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
    monkeypatch.setattr(writer_module, "time", fake)
    return fake


@pytest.fixture
def parts(tmp_path, clock):
    box = SqliteOutbox(str(tmp_path / "outbox.sqlite"))
    client = FakeClient()
    writer = LearningWriter(box, client, interval=0.01, max_attempts=3)
    yield box, client, writer
    box.close()


def queue_a_decision_with_events(box, events: int = 2, tag: str = "") -> None:
    box.enqueue("decision", f"d{tag}", {"decision_id": f"d{tag}", "symbol": "TRX/USDT"})
    for i in range(events):
        box.enqueue("event", f"e{tag}{i}", {"decision_id": f"d{tag}", "event_type": f"step{i}"})


def test_a_decision_is_shipped_before_its_events(parts):
    box, client, writer = parts
    queue_a_decision_with_events(box)
    assert writer.drain() == 3
    assert [(table, len(rows)) for table, rows, _ in client.calls] == [
        ("trading_decisions", 1), ("trading_events", 2),
    ]
    assert {conflict for _, _, conflict in client.calls} == {"idempotency_key"}
    assert writer.written_total == 3
    assert box.stats()["outbox_pending"] == 0
    assert writer.last_success_at


def test_an_outage_leaves_every_row_waiting_and_charges_none_of_them(parts, clock):
    box, client, writer = parts
    queue_a_decision_with_events(box)
    client.down = True
    assert writer.drain() == 0
    assert writer.outages_total == 1
    assert writer.failed_total == 0
    assert [row.attempts for row in box.claim()] == [0, 0, 0]
    assert client.calls == []

    client.down = False
    assert writer.drain() == 0  # still paused
    clock.advance(OUTAGE_BACKOFF_MIN)
    assert writer.drain() == 3
    assert [table for table, _, _ in client.calls] == ["trading_decisions", "trading_events"]


def test_the_pause_after_an_outage_doubles_to_a_minute_and_resets_on_success(parts, clock):
    box, client, writer = parts
    queue_a_decision_with_events(box)
    client.down = True
    pauses = []
    for _ in range(8):
        writer.drain()
        pauses.append(writer.paused_for())
        clock.advance(writer.paused_for())
    assert pauses == [1, 2, 4, 8, 16, 32, 60, 60]
    assert writer.outages_total == 8

    client.down = False
    assert writer.drain() == 3
    assert writer.paused_for() == 0
    client.down = True
    queue_a_decision_with_events(box, tag="b")
    writer.drain()
    assert writer.paused_for() == OUTAGE_BACKOFF_MIN  # back to the start after a success


def test_a_row_the_database_refuses_is_isolated_and_the_good_ones_go_through(parts):
    box, client, writer = parts
    box.enqueue("event", "ok1", {"n": 1})
    box.enqueue("event", "bad", {"n": 2, "poison": True})
    box.enqueue("event", "ok2", {"n": 3})
    client.refuse = lambda payload: payload.get("poison", False)

    assert writer.drain() == 1
    assert [rows for _, rows, _ in client.calls] == [[{"n": 1}]]
    assert writer.failed_total == 1
    assert writer.retried_total == 1
    assert writer.quarantined_total == 0
    assert writer.outages_total == 0
    assert box.claim() == []  # ok2 waits behind the row that is backing off
    assert box.stats()["outbox_pending"] == 2


def test_a_row_refused_repeatedly_is_quarantined_and_the_queue_moves_on(parts, clock):
    box, client, writer = parts  # max_attempts=3
    box.enqueue("event", "bad", {"poison": True})
    box.enqueue("event", "ok", {"n": 1})
    client.refuse = lambda payload: payload.get("poison", False)

    for wait in (1, 2, 4):
        writer.drain()
        clock.advance(wait)
    assert writer.quarantined_total == 1
    assert client.calls == []

    assert writer.drain() == 1
    assert client.calls == [("trading_events", [{"n": 1}], "idempotency_key")]
    assert writer.failed_total == 3
    assert writer.retried_total == 2
    assert box.stats()["outbox_pending"] == 0
    assert box.stats()["outbox_quarantined"] == 1


def test_a_refusal_of_a_single_row_needs_no_isolation(parts):
    box, client, writer = parts
    box.enqueue("event", "bad", {"poison": True})
    client.refuse = lambda payload: payload.get("poison", False)
    assert writer.drain() == 0
    assert writer.failed_total == 1
    assert client.calls == []
    assert "invalid input" in writer.last_error


def test_a_record_queued_twice_reaches_the_database_once(parts):
    box, client, writer = parts
    box.enqueue("event", "e", {"n": 1})
    box.enqueue("event", "e", {"n": 1})
    assert writer.drain() == 1
    assert client.calls == [("trading_events", [{"n": 1}], "idempotency_key")]


def test_a_payload_that_will_not_decode_is_set_aside_without_stopping_the_queue(parts):
    box, client, writer = parts
    box.enqueue("event", "ok1", {"n": 1})
    box._connection().execute(
        "insert into outbox (kind, idempotency_key, payload, created_at) "
        "values ('event', 'broken', '{not json', 0)"
    )
    box.enqueue("event", "ok2", {"n": 2})

    assert writer.drain() == 1  # ok1 ships; broken is quarantined; ok2 waits a pass
    assert writer.quarantined_total == 1
    assert writer.drain() == 1
    assert [rows[0]["n"] for _, rows, _ in client.calls] == [1, 2]
    assert box.stats()["outbox_quarantined"] == 1


def test_a_kind_the_writer_does_not_know_is_set_aside(parts):
    box, client, writer = parts
    box.enqueue("mystery", "m", {})
    box.enqueue("event", "e", {"n": 1})
    assert writer.drain() == 1
    assert writer.quarantined_total == 1
    assert box.stats()["outbox_quarantined"] == 1
    assert "unknown kind" in writer.last_error


def test_flush_ships_everything_or_says_it_could_not(parts, clock):
    box, client, writer = parts
    queue_a_decision_with_events(box)
    assert writer.flush(timeout=5) is True
    assert box.stats()["outbox_pending"] == 0

    queue_a_decision_with_events(box, tag="b")
    client.down = True
    assert writer.flush(timeout=3) is False
    assert box.stats()["outbox_pending"] == 3
    assert clock.now <= 1_000.0 + 3 + 0.5  # it gave up at the deadline, not later


def test_flush_makes_one_more_attempt_even_while_paused(parts, clock):
    box, client, writer = parts
    queue_a_decision_with_events(box)
    client.down = True
    writer.drain()
    assert writer.paused_for() > 0
    client.down = False
    assert writer.flush(timeout=1) is True


def test_health_says_what_the_writer_has_done(parts):
    box, client, writer = parts
    health = writer.health()
    for key in ("outbox_pending", "outbox_oldest_age_seconds", "outbox_quarantined", "dropped_total",
                "breaker_open", "written_total", "failed_total", "retried_total", "quarantined_total",
                "outages_total", "paused_for_seconds", "last_success_at", "writer_error", "writer_alive"):
        assert key in health
    assert health["writer_alive"] is False
    assert health["paused_for_seconds"] == 0


def test_is_the_rows_fault_tells_a_refusal_from_an_outage():
    for status in (400, 409, 422):
        assert is_the_rows_fault(Refused(status))
    for status in (300, 401, 403, 404, 500, 502, 503):
        assert not is_the_rows_fault(Refused(status))
    assert not is_the_rows_fault(Unreachable("timed out"))


def test_the_thread_drains_on_its_own(tmp_path):
    box = SqliteOutbox(str(tmp_path / "threaded.sqlite"))
    client = FakeClient()
    writer = LearningWriter(box, client, interval=0.02)
    writer.start()
    try:
        box.enqueue("event", "e", {"n": 1})
        deadline = real_time.monotonic() + 5
        while writer.written_total < 1 and real_time.monotonic() < deadline:
            real_time.sleep(0.02)
        assert writer.written_total == 1
        assert client.calls == [("trading_events", [{"n": 1}], "idempotency_key")]
    finally:
        writer.stop()
        writer.join(timeout=2)
        box.close()


def test_the_front_door_is_idempotent_and_safe_before_anything_started(tmp_path, monkeypatch):
    monkeypatch.setattr(learning, "_state", {"outbox": None, "writer": None, "recorder": None, "adapter": None})
    assert learning.health() == {"enabled": False}
    assert learning.flush() is True

    client = FakeClient()
    writer = learning.start(str(tmp_path / "front.sqlite"), client, interval=0.02)
    try:
        assert learning.start(str(tmp_path / "other.sqlite"), client) is writer
        assert learning.health()["enabled"] is True
        assert learning.current_writer() is writer
        learning.current_outbox().enqueue("event", "e", {"n": 1})
        assert learning.flush(timeout=5) is True
        assert client.calls[0] == ("trading_events", [{"n": 1}], "idempotency_key")
    finally:
        writer.stop()
        writer.join(timeout=2)
        learning.current_outbox().close()


def test_importing_every_submodule_leaves_the_front_door_intact():
    """A submodule named like a package function replaces it on import: the
    package once had outbox(), writer() and health() next to outbox.py,
    writer.py and health.py, and lost all three the moment they were used."""
    for module in pkgutil.iter_modules(learning.__path__):
        importlib.import_module(f"app.learning.{module.name}")
    for name in ("start", "flush", "health", "current_outbox", "current_writer"):
        assert callable(getattr(learning, name)), name


def test_the_modules_log_lines_are_flushed(capsys):
    """Render buffers a plain print; a report that arrives an hour late is no report."""
    import io

    lines = []

    class Pipe(io.StringIO):
        def flush(self):
            lines.append(self.getvalue())
            super().flush()

    import sys
    real = sys.stdout
    sys.stdout = Pipe()
    try:
        learning.say("learning: hello")
    finally:
        sys.stdout = real
    assert lines and lines[-1].startswith("learning: hello")
