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
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.backtest import periods
from app.backtest.runner import BacktestError, BacktestRequest, run_backtest
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._run, daemon=True, name="heartbeat")
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._client.update(
                    "backtest_jobs",
                    {"heartbeat_at": _now()},
                    filters={"id": f"eq.{self._job_id}"},
                )
            except Exception as exc:  # never let a heartbeat kill the run
                log.warning("heartbeat failed: %s", exc)


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
    ("extending history backwards", "download_history"),
    ("fetching recent candles", "download_recent"),
    ("downloading", "download_recent"),
    ("running freqtrade backtesting", "backtesting"),
    ("parsing", "parsing"),
)


def _stage_for(message: str) -> str | None:
    lowered = message.lower()
    for needle, stage in _STAGE_HINTS:
        if needle in lowered:
            return stage
    return None


def _progress(client: SupabaseClient, job_id: str):
    def report(message: str, *, stage: str | None = None) -> None:
        log.info("job %s: %s", job_id, message)
        stage = stage or _stage_for(message)
        values: dict[str, Any] = {"progress": message[:500], "heartbeat_at": _now()}
        if stage and stage in STAGE_PROGRESS:
            values["stage"] = stage
            values["progress_pct"] = STAGE_PROGRESS[stage][0]
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

        with Heartbeat(client, job_id, settings.worker.heartbeat_seconds):
            artifacts = run_backtest(
                request,
                data_dir=settings.worker.data_dir,
                user_dir=settings.worker.user_dir,
                timeout_seconds=settings.worker.job_timeout_seconds,
                progress=report,
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
            revived = client.rpc("requeue_stalled_backtest_jobs")
            if revived:
                log.info("requeued %s stalled job(s)", revived)
        except Exception as exc:
            log.warning("could not requeue stalled jobs: %s", exc)

    sweep_stalled()
    last_sweep = time.monotonic()

    idle_logged = False
    while not _stopping.is_set():
        if time.monotonic() - last_sweep >= STALL_SWEEP_SECONDS:
            sweep_stalled()
            last_sweep = time.monotonic()

        try:
            job = client.rpc("claim_backtest_job", {"p_worker": worker_name})
        except Exception as exc:
            log.error("could not claim a job: %s", exc)
            _stopping.wait(settings.worker.poll_interval_seconds)
            continue

        if not job or not job.get("id"):
            if not idle_logged:
                log.info("queue empty; waiting")
                idle_logged = True
            _stopping.wait(settings.worker.poll_interval_seconds)
            continue

        idle_logged = False
        log.info("claimed job %s (%s)", job["id"], job.get("builtin_strategy") or "builder strategy")
        process(client, job)

    log.info("worker %s stopped", worker_name)


def _handle_signal(signum: int, _frame: Any) -> None:
    log.info("signal %s received; finishing the current job then stopping", signum)
    _stopping.set()


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
