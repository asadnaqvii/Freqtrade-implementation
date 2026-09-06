"""A worker being shut down must hand its job back, not abandon it.

Render restarts the worker on every deploy. The job it was running stayed in
`running` with no process behind it until the stall sweep noticed five minutes
later -- and that five-minute window is what "the backtest is stuck" looked
like from the page. A worker that is being told to stop knows it is abandoning
the job; there is no reason to make anyone wait for a timeout to rediscover it.

Observed on 2026-08-20: worker claimed a job at 10:04:40, launched freqtrade at
10:05:12, and Render restarted the instance at 10:05:24 for a deploy. The
replacement swept for stalled jobs two seconds later, found the heartbeat fresh,
and moved on -- leaving the job running at 60% with nothing running it.
"""

from __future__ import annotations

import pytest

from app.worker import main as worker


class Client:
    def __init__(self, boom=None):
        self.boom = boom
        self.updates = []

    def update(self, table, values, *, filters=None):
        if self.boom:
            raise self.boom
        self.updates.append((table, values, dict(filters or {})))
        return [values]


@pytest.fixture(autouse=True)
def clean():
    worker._in_flight.update(client=None, job_id=None)
    worker._stopping.clear()
    yield
    worker._in_flight.update(client=None, job_id=None)
    worker._stopping.clear()


def test_a_shutdown_returns_the_job_to_the_queue():
    client = Client()
    worker._in_flight.update(client=client, job_id="job-1")
    worker.release_in_flight("worker stopped")

    table, values, filters = client.updates[0]
    assert table == "backtest_jobs"
    assert values["status"] == "queued"
    assert values["claimed_by"] is None
    assert filters["id"] == "eq.job-1"


def test_it_only_releases_a_job_that_is_still_running():
    """Never resurrect one that finished between the signal and the write."""
    client = Client()
    worker._in_flight.update(client=client, job_id="job-1")
    worker.release_in_flight("stopped")
    _, _, filters = client.updates[0]
    assert filters.get("status") == "eq.running", (
        "without this a job that completed microseconds earlier would be requeued "
        "and run a second time"
    )


def test_progress_is_reset_so_the_next_worker_does_not_inherit_a_stale_percentage():
    client = Client()
    worker._in_flight.update(client=client, job_id="job-1")
    worker.release_in_flight("stopped")
    _, values, _ = client.updates[0]
    assert values["progress_pct"] == 0 and values["stage"] == "queued"
    assert "requeued" in values["progress"]


def test_releasing_twice_writes_once():
    """The signal can arrive while the finally block is already running."""
    client = Client()
    worker._in_flight.update(client=client, job_id="job-1")
    worker.release_in_flight("first")
    worker.release_in_flight("second")
    assert len(client.updates) == 1


def test_nothing_in_flight_is_not_an_error():
    client = Client()
    worker._in_flight.update(client=client, job_id=None)
    worker.release_in_flight("idle")
    assert client.updates == []


def test_a_failed_release_does_not_stop_the_shutdown():
    """The stall sweep is still the backstop; this is only the fast path."""
    worker._in_flight.update(client=Client(boom=RuntimeError("postgrest down")),
                             job_id="job-1")
    worker.release_in_flight("stopped")   # must not raise


def test_the_signal_handler_both_stops_and_releases():
    client = Client()
    worker._in_flight.update(client=client, job_id="job-1")
    worker._handle_signal(15, None)
    assert worker._stopping.is_set()
    assert client.updates and client.updates[0][1]["status"] == "queued"


def test_a_stopping_worker_asks_the_backtest_to_stop():
    """Otherwise freqtrade runs until SIGKILL, possibly mid-write."""
    import inspect

    src = inspect.getsource(worker.process)
    assert "_stopping.is_set()" in src, (
        "should_stop must consider the shutdown flag, not only job cancellation"
    )


# ---------------------------------------------------------------------------
# Memory, which is the thing nothing else reports
# ---------------------------------------------------------------------------

def test_the_memory_limit_is_read_from_the_cgroup(tmp_path, monkeypatch):
    limit = tmp_path / "memory.max"
    limit.write_text("536870912\n")            # 512Mi, what Render's starter gives
    monkeypatch.setattr(worker, "Path", lambda p: limit if "memory.max" in str(p)
                        else type("Missing", (), {"read_text": staticmethod(
                            lambda: (_ for _ in ()).throw(OSError()))})())
    assert worker.memory_limit_mb() == 512


def test_an_unlimited_cgroup_reports_nothing_rather_than_a_huge_number():
    """cgroup v1 writes a sentinel near 2**63 when there is no limit.

    Reporting that as a limit would put "memory limit 8796093022207 MB" in the
    log, which is worse than saying nothing.
    """
    import pathlib

    class Fake:
        def __init__(self, value):
            self.value = value

        def read_text(self):
            return self.value

    for sentinel in ("max", "9223372036854771712", "", "-1", "not a number"):
        saved = worker.Path
        worker.Path = lambda _p, v=sentinel: Fake(v)
        try:
            assert worker.memory_limit_mb() is None, sentinel
        finally:
            worker.Path = saved
