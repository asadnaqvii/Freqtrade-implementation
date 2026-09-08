"""Backtest worker.

Claims jobs from the Supabase queue, runs them through freqtrade, and writes the
results back. Safe to run more than one: claim_backtest_job() uses
FOR UPDATE SKIP LOCKED, so N workers drain the queue without ever taking the
same job.

Run with:  python -m app.worker.main
"""

from __future__ import annotations

import logging
import os
import re
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backtest import periods
from app.backtest.runner import (
    BacktestError, BacktestRequest, Cancelled, run_backtest,
)
from app.core.config import get_settings
from app.core.supabase import SupabaseClient, SupabaseError

log = logging.getLogger("worker")

# Where the repo's own strategies live, for jobs that name a built-in.
BUILTIN_STRATEGY_DIR = Path(__file__).resolve().parents[2] / "strategies"

_stopping = threading.Event()

#: How often to look for jobs abandoned by a dead worker. Shorter than the
#: staleness window the database uses, so a stalled job is always seen by some
#: sweep rather than depending on a restart happening to land at the right time.
STALL_SWEEP_SECONDS = 120

#: How often to check whether the trading bot is still alive. Shorter than the
#: five minutes v_bot_health uses to call a bot stale, so the first sweep after
#: it goes quiet is the one that reports it rather than the one after that.
BOT_WATCH_SECONDS = 60

#: Longest the queue poll backs off to while nothing is queued. The poll ran at
#: a flat 10s and made 128,897 `claim_backtest_job` calls in sixteen days --
#: 465 seconds of database CPU and 773k buffer reads, essentially all of it
#: asking an empty queue whether it was still empty. On a Micro instance that
#: contends with the trading bot for the same CPU and disk, so an idle worker
#: now goes quiet and wakes up promptly the moment there is work.
IDLE_MAX_POLL_SECONDS = 60

#: How often to trim the audit log, and how much of it to keep. Before migration
#: 0020 the audit trigger recorded every heartbeat, and security_events reached
#: 70 MB of a 100 MB database with nobody watching. The trigger no longer writes
#: those, but an audit log with no retention only ever grows, so trim it here.
LOG_PRUNE_SECONDS = 6 * 60 * 60
LOG_KEEP_DAYS = 90


#: How quiet a running job must go before it is considered abandoned. The
#: database default is twenty minutes, which is fine for a crash and far too
#: slow for a deploy: a redeploy orphans whatever the old worker was holding,
#: and that job then looked stuck for a third of an hour. Comfortably more than
#: the heartbeat interval, so a job that is merely slow is never taken away
#: from a worker still doing it.
STALE_AFTER = "5 minutes"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def memory_limit_mb() -> int | None:
    """This container's memory ceiling, if the kernel will say.

    Worth knowing out loud. A backtest that exceeds it does not raise, log, or
    fail: the kernel kills the process, the platform restarts the service, the
    stall sweep requeues the job, and it happens again -- three times, and then
    the job is marked "worker stopped reporting". Nothing in that chain ever
    mentions memory, so the one number that explains it is the one number
    nobody has.
    """
    for path in ("/sys/fs/cgroup/memory.max",                       # cgroup v2
                 "/sys/fs/cgroup/memory/memory.limit_in_bytes"):    # cgroup v1
        try:
            raw = Path(path).read_text().strip()
        except OSError:
            continue
        if raw in ("max", ""):
            continue
        try:
            value = int(raw)
        except ValueError:
            continue
        # cgroup v1 reports a sentinel near 2**63 when there is no limit.
        if value <= 0 or value > (1 << 50):
            continue
        return value // (1024 * 1024)
    return None


def memory_used_mb() -> int | None:
    """Resident memory of this container right now, if the kernel will say."""
    for path in ("/sys/fs/cgroup/memory.current",
                 "/sys/fs/cgroup/memory/memory.usage_in_bytes"):
        try:
            return int(Path(path).read_text().strip()) // (1024 * 1024)
        except (OSError, ValueError):
            continue
    return None


class Heartbeat:
    """Keeps heartbeat_at fresh while a job runs.

    Without this, requeue_stalled_backtest_jobs() cannot tell a long backtest
    from a dead worker, and would either retry work still in progress or leave a
    genuinely dead job stuck forever.
    """

    def __init__(self, client: SupabaseClient, job_id: str, interval: int) -> None:
        self._client = client
        self._job_id = job_id
        self._interval = interval
        self._stop = threading.Event()
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True, name="heartbeat")
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    @property
    def cancelled(self) -> bool:
        """Whether someone marked this job cancelled while it was running."""
        return self._cancelled.is_set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                rows = self._client.update(
                    "backtest_jobs",
                    {"heartbeat_at": _now()},
                    filters={"id": f"eq.{self._job_id}"},
                )
            except Exception as exc:  # never let a heartbeat kill the run
                log.warning("heartbeat failed: %s", exc)
                continue

            # The heartbeat already round-trips to this row every few seconds,
            # so it is also where a cancellation shows up soonest -- no second
            # poller, no extra request.
            status = (rows[0] if rows else {}).get("status")
            if status == "cancelled" and not self._cancelled.is_set():
                log.info("job %s was cancelled; stopping", self._job_id)
                self._cancelled.set()


#: Where each stage sits on the 0-100 bar. Assigned, not measured: freqtrade
#: reports no completion figure, and inventing a smooth one would be a lie that
#: looks like information. The two long stages own most of the range because
#: they own most of the wall clock -- a bar that sits at 40% for four minutes
#: and then sprints is telling the truth about where the time goes.
STAGE_PROGRESS: dict[str, tuple[int, str]] = {
    "claimed":          (3,   "Claimed by a worker"),
    "strategy":         (8,   "Preparing the strategy"),
    "download_history": (15,  "Downloading history"),
    "download_recent":  (45,  "Downloading recent candles"),
    "backtesting":      (60,  "Running the backtest"),
    "parsing":          (88,  "Reading the results"),
    "storing":          (94,  "Saving trades and equity curve"),
    "done":             (100, "Finished"),
}

#: Substrings the runner emits, mapped to a stage. Kept here rather than passing
#: stage keys through the runner so the runner stays a plain subprocess driver.
_STAGE_HINTS = (
    ("downloading history —", "download_history"),
    ("extending history backwards", "download_history"),
    ("fetching recent candles", "download_recent"),
    ("downloading", "download_recent"),
    ("running freqtrade backtesting", "backtesting"),
    ("parsing", "parsing"),
)


_SHARE_RE = re.compile(r"\((\d+)%\)")


def _stage_for(message: str) -> str | None:
    lowered = message.lower()
    for needle, stage in _STAGE_HINTS:
        if needle in lowered:
            return stage
    return None


def _progress_within(stage: str, message: str) -> int | None:
    """Turn "(43%)" inside a download message into a point on the whole bar.

    Backfilling ten years of candles is most of the wall clock, so the bar has
    to move inside that stage or it reads as hung -- which is exactly how it
    read sitting at 15 for minutes on end.
    """
    if stage != "download_history":
        return None
    found = _SHARE_RE.search(message)
    if not found:
        return None
    share = max(0, min(100, int(found.group(1)))) / 100
    low, high = STAGE_PROGRESS["download_history"][0], STAGE_PROGRESS["download_recent"][0]
    return int(low + (high - low) * share)


def _progress(client: SupabaseClient, job_id: str):
    def report(message: str, *, stage: str | None = None) -> None:
        log.info("job %s: %s", job_id, message)
        stage = stage or _stage_for(message)
        values: dict[str, Any] = {"progress": message[:500], "heartbeat_at": _now()}
        if stage and stage in STAGE_PROGRESS:
            values["stage"] = stage
            within = _progress_within(stage, message)
            values["progress_pct"] = (
                within if within is not None else STAGE_PROGRESS[stage][0]
            )
        try:
            client.update("backtest_jobs", values, filters={"id": f"eq.{job_id}"})
        except Exception as exc:
            log.warning("could not record progress: %s", exc)

    return report


def resolve_strategy(client: SupabaseClient, job: dict[str, Any]) -> tuple[str, str | None, str | None]:
    """Work out what to run: (class name, generated source, builtin directory)."""
    version_id = job.get("strategy_version_id")
    if version_id:
        version = client.select_one(
            "strategy_versions",
            # The FK is named explicitly: strategy_specs and strategy_versions
            # reference each other (strategy_id one way, current_version_id the
            # other), so an unqualified embed is ambiguous and PostgREST refuses
            # it rather than guessing.
            columns="id,version,generated_code,compiles,compile_error,strategy_id,"
                    "strategy_specs!strategy_versions_strategy_id_fkey(name,class_name)",
            filters={"id": f"eq.{version_id}"},
        )
        if not version:
            raise BacktestError(f"strategy version {version_id} no longer exists")
        if version.get("compiles") is False:
            raise BacktestError(
                "this strategy version does not compile, so backtesting it would "
                f"fail the same way: {version.get('compile_error')}"
            )
        source = version.get("generated_code")
        if not source:
            raise BacktestError(
                f"strategy version {version_id} has no generated code; recompile it first"
            )
        parent = version.get("strategy_specs") or {}
        class_name = parent.get("class_name")
        if not class_name:
            raise BacktestError(f"strategy version {version_id} has no class name")
        return class_name, source, None

    builtin = job.get("builtin_strategy")
    if not builtin:
        raise BacktestError("job names neither a strategy version nor a built-in strategy")

    candidate = BUILTIN_STRATEGY_DIR / f"{builtin}.py"
    if not candidate.exists():
        available = sorted(p.stem for p in BUILTIN_STRATEGY_DIR.glob("*.py"))
        raise BacktestError(
            f"no built-in strategy called {builtin}. Available: {', '.join(available)}"
        )
    return builtin, None, str(BUILTIN_STRATEGY_DIR)


def store_results(client: SupabaseClient, job: dict[str, Any], artifacts) -> str:
    """Write the run, its per-pair rows, its trades and its equity curve."""
    export = artifacts.export

    run_row = export.run_row(
        job_id=job["id"],
        owner_id=job.get("owner_id"),
        strategy_id=job.get("strategy_id"),
        strategy_version_id=job.get("strategy_version_id"),
        exchange=job.get("exchange") or "kucoin",
        freqtrade_version=artifacts.freqtrade_version,
        duration_seconds=round(artifacts.duration_seconds, 3),
        started_at=job.get("started_at"),
        finished_at=_now(),
        config=export.config or {},
    )
    # A job may not have specified pairs the export knows about; prefer the export.
    if not run_row.get("pairs"):
        run_row["pairs"] = list(job.get("pairs") or [])

    # Record the gap between the window asked for and the window that ran. Left
    # unrecorded, a ten-year request that found one month of candles looks
    # exactly like a ten-year request that succeeded.
    run_row.update(periods.coverage(
        job.get("timerange"),
        run_row.get("timerange_start"),
        run_row.get("timerange_end"),
        timeframe=run_row.get("timeframe") or job.get("timeframe"),
    ))
    if run_row.get("coverage_note"):
        log.warning("job %s: %s", job["id"], run_row["coverage_note"])

    inserted = client.insert("backtest_runs", run_row)
    run_id = inserted[0]["id"]

    pair_rows = export.pair_rows(run_id)
    if pair_rows:
        client.insert("backtest_pair_results", pair_rows, returning=False)

    trade_count = client.insert_chunked("backtest_trades", export.trade_rows(run_id))

    equity_rows = export.equity_rows(run_id)
    if equity_rows:
        client.insert_chunked("backtest_equity_curve", equity_rows)

    log.info(
        "job %s -> run %s (%s trades, %s pairs, %s equity points)",
        job["id"], run_id, trade_count, len(pair_rows), len(equity_rows),
    )
    return run_id


def process(client: SupabaseClient, job: dict[str, Any]) -> None:
    settings = get_settings()
    job_id = job["id"]
    report = _progress(client, job_id)

    try:
        report("Preparing the strategy", stage="strategy")
        class_name, source, builtin_dir = resolve_strategy(client, job)
        request = BacktestRequest.from_job(
            job, strategy_source=source, strategy_name=class_name, strategy_path=builtin_dir
        )

        with Heartbeat(client, job_id, settings.worker.heartbeat_seconds) as beat:
            artifacts = run_backtest(
                request,
                data_dir=settings.worker.data_dir,
                user_dir=settings.worker.user_dir,
                timeout_seconds=settings.worker.job_timeout_seconds,
                progress=report,
                should_stop=lambda: beat.cancelled or _stopping.is_set(),
            )
            report("Saving trades and equity curve", stage="storing")
            run_id = store_results(client, job, artifacts)

        client.update(
            "backtest_jobs",
            {
                "status": "completed",
                "finished_at": _now(),
                "progress": f"completed; run {run_id}",
                "stage": "done",
                "progress_pct": 100,
                "error": None,
            },
            filters={"id": f"eq.{job_id}"},
        )

    except Cancelled:
        # Already marked cancelled by whoever asked; just stop and say so.
        log.info("job %s stopped on request", job_id)
        try:
            client.update(
                "backtest_jobs",
                {"status": "cancelled", "finished_at": _now(),
                 "progress": "cancelled", "stage": "done", "progress_pct": 100},
                filters={"id": f"eq.{job_id}"},
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("could not record the cancellation: %s", exc)
    except BacktestError as exc:
        _fail(client, job, str(exc))
    except SupabaseError as exc:
        # A database problem is ours, not the user's; keep the detail short.
        _fail(client, job, f"could not record results: {exc}")
    except Exception as exc:
        log.exception("job %s crashed", job_id)
        _fail(client, job, f"unexpected {type(exc).__name__}: {exc}")


def _fail(client: SupabaseClient, job: dict[str, Any], message: str) -> None:
    """Mark a job failed, or leave it for another attempt if retries remain."""
    attempts = int(job.get("attempts") or 0)
    max_attempts = int(job.get("max_attempts") or 3)
    exhausted = attempts >= max_attempts

    log.error("job %s failed (attempt %s/%s): %s", job["id"], attempts, max_attempts, message)
    try:
        client.update(
            "backtest_jobs",
            {
                "status": "failed" if exhausted else "queued",
                "error": message[:4000],
                "finished_at": _now() if exhausted else None,
                "claimed_by": None,
                "claimed_at": None,
            },
            filters={"id": f"eq.{job['id']}"},
        )
    except Exception as exc:
        log.error("could not even record the failure: %s", exc)


def run_forever() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    client = SupabaseClient.service()
    worker_name = settings.worker.name
    log.info("worker %s starting; polling every %ss", worker_name, settings.worker.poll_interval_seconds)
    limit = memory_limit_mb()
    if limit:
        log.info("memory limit %s MB (in use %s MB)", limit, memory_used_mb())

    for directory in (settings.worker.data_dir, settings.worker.user_dir):
        Path(directory).mkdir(parents=True, exist_ok=True)

    def sweep_stalled() -> None:
        """Return jobs whose worker died mid-run to the queue.

        This used to run only at startup, and that is not enough: a job orphaned
        by a deploy is not yet stale when the replacement boots seconds later, so
        the one check it got found nothing and nothing ever looked again. A job
        could then sit in `running` forever, with no process behind it -- which
        is exactly what happened across a run of redeploys.
        """
        try:
            revived = client.rpc("requeue_stalled_backtest_jobs",
                                 {"p_stale_after": STALE_AFTER})
            if revived:
                log.info("requeued %s stalled job(s)", revived)
        except Exception as exc:
            log.warning("could not requeue stalled jobs: %s", exc)

    def sweep_bots() -> None:
        """Notice the trading bot going quiet, and say so.

        Runs here rather than in the bot because a watchdog inside the process
        it watches is gone at the one moment it matters. The worker is a
        separate always-on service, so it is still around when the bot is what
        died.
        """
        try:
            from app.worker import watchdog

            watchdog.sweep(client, webhook_url=settings.worker.alert_webhook_url)
        except Exception as exc:  # noqa: BLE001 - never take the worker down for this
            log.warning("bot watchdog failed: %s", exc)

    def prune_logs() -> None:
        """Trim the audit log to its retention window."""
        try:
            removed = client.rpc("prune_security_events",
                                 {"p_keep_days": LOG_KEEP_DAYS})
            if removed:
                log.info("pruned %s expired security event(s)", removed)
        except Exception as exc:  # noqa: BLE001 - housekeeping is never fatal
            log.warning("could not prune security events: %s", exc)

    sweep_stalled()
    sweep_bots()
    prune_logs()
    last_sweep = time.monotonic()
    last_watch = time.monotonic()
    last_prune = time.monotonic()

    idle_logged = False
    base_poll = max(int(settings.worker.poll_interval_seconds), 1)
    idle_poll = base_poll
    while not _stopping.is_set():
        if time.monotonic() - last_sweep >= STALL_SWEEP_SECONDS:
            sweep_stalled()
            last_sweep = time.monotonic()

        if time.monotonic() - last_watch >= BOT_WATCH_SECONDS:
            sweep_bots()
            last_watch = time.monotonic()

        if time.monotonic() - last_prune >= LOG_PRUNE_SECONDS:
            prune_logs()
            last_prune = time.monotonic()

        try:
            job = client.rpc("claim_backtest_job", {"p_worker": worker_name})
        except Exception as exc:
            log.error("could not claim a job: %s", exc)
            _stopping.wait(idle_poll)
            idle_poll = min(idle_poll * 2, IDLE_MAX_POLL_SECONDS)
            continue

        if not job or not job.get("id"):
            if not idle_logged:
                log.info("queue empty; backing off to %ss between checks",
                         IDLE_MAX_POLL_SECONDS)
                idle_logged = True
            _stopping.wait(idle_poll)
            # Back off while the queue stays empty. Doubling rather than jumping
            # straight to the ceiling keeps a job queued moments after the last
            # one finished from waiting the full interval.
            idle_poll = min(idle_poll * 2, IDLE_MAX_POLL_SECONDS)
            continue

        idle_logged = False
        idle_poll = base_poll
        log.info("claimed job %s (%s); memory %s/%s MB", job["id"],
                 job.get("builtin_strategy") or "builder strategy",
                 memory_used_mb(), memory_limit_mb())

        # A job that has already been cut short twice is the shape an
        # out-of-memory kill makes: no error is ever recorded, because nothing
        # gets the chance to record one.
        if int(job.get("attempts") or 0) >= 2:
            log.warning(
                "job %s has been restarted %s time(s) with no error recorded. That is "
                "what an out-of-memory kill looks like from here: the kernel stops the "
                "process, the platform restarts the service, and the job comes back. "
                "Memory limit is %s MB. If this fails again, the backtest needs a "
                "shorter window, fewer pairs, a coarser timeframe, or a larger worker.",
                job["id"], job.get("attempts"), memory_limit_mb(),
            )
        _in_flight.update(client=client, job_id=job["id"])
        try:
            process(client, job)
        finally:
            _in_flight["job_id"] = None

    log.info("worker %s stopped", worker_name)


#: The job this worker is holding, so a shutdown can hand it back rather than
#: abandon it. Set before process() and cleared after.
_in_flight: dict[str, Any] = {"client": None, "job_id": None}


def release_in_flight(reason: str) -> None:
    """Return the job this worker is holding to the queue, now.

    A worker being shut down knows it is abandoning the job. Without this the
    job sits in `running` with no process behind it until the stall sweep
    notices five minutes later -- which is exactly what "the backtest is stuck"
    looked like. Handing it back makes the next worker pick it up on its next
    ten-second poll.

    Best effort and deliberately small: this runs from a signal handler, with a
    SIGKILL following in seconds.
    """
    client, job_id = _in_flight.get("client"), _in_flight.get("job_id")
    if not client or not job_id:
        return
    _in_flight["job_id"] = None
    try:
        client.update(
            "backtest_jobs",
            {"status": "queued", "claimed_by": None, "claimed_at": None,
             "started_at": None, "heartbeat_at": None,
             "progress": f"requeued: {reason}", "progress_pct": 0, "stage": "queued"},
            filters={"id": f"eq.{job_id}", "status": "eq.running"},
        )
        log.info("released job %s back to the queue (%s)", job_id, reason)
    except Exception as exc:  # noqa: BLE001 - the stall sweep is still the backstop
        log.warning("could not release job %s: %s", job_id, exc)


def _handle_signal(signum: int, _frame: Any) -> None:
    log.info("signal %s received; releasing the current job and stopping", signum)
    _stopping.set()
    release_in_flight(f"worker stopped by signal {signum}")


def main() -> int:
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        run_forever()
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
