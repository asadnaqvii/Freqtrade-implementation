"""Queue backtests and read their results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.deps import CurrentUser, UserDB
from app.backtest import periods, verdict
from app.backtest.runner import BacktestRequest

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


class BacktestCreate(BaseModel):
    """A backtest request.

    Exchange, pairs, timeframe, quote currency and window are all free: any
    venue ccxt supports, any pair it lists, any past period it has history for.
    """

    strategy_version_id: str | None = None
    builtin_strategy: str | None = None
    exchange: str = "kucoin"
    timeframe: str = "5m"
    pairs: list[str] = Field(min_length=1, max_length=50)
    timerange: str | None = None
    stake_currency: str = "USDT"
    stake_amount: float | None = None
    starting_balance: float = Field(default=1000, gt=0)
    max_open_trades: int = Field(default=3, ge=1, le=50)
    fee: float | None = Field(default=None, ge=0, le=0.1)
    enable_protections: bool = False
    download_data: bool = True
    priority: int = Field(default=100, ge=1, le=1000)

    @model_validator(mode="after")
    def _one_strategy_source(self) -> "BacktestCreate":
        if bool(self.strategy_version_id) == bool(self.builtin_strategy):
            raise ValueError(
                "give exactly one of strategy_version_id or builtin_strategy"
            )
        return self

    @model_validator(mode="after")
    def _sane_request(self) -> "BacktestCreate":
        # Validate here rather than letting the worker discover it: a job that
        # cannot possibly run should be refused at submission, while the person
        # who wrote it is still looking at the screen.
        probe = BacktestRequest(
            strategy_name=self.builtin_strategy or "Placeholder",
            exchange=self.exchange,
            timeframe=self.timeframe,
            pairs=self.pairs,
            timerange=self.timerange,
            stake_currency=self.stake_currency,
            max_open_trades=self.max_open_trades,
        )
        try:
            probe.validate()
        except Exception as exc:
            raise ValueError(str(exc)) from exc
        return self


@router.post("", status_code=202)
async def queue_backtest(body: BacktestCreate, user: CurrentUser, db: UserDB) -> dict:
    payload = body.model_dump(exclude_none=True)
    payload["owner_id"] = user.profile_id

    if body.strategy_version_id:
        version = db.select_one(
            "strategy_versions",
            columns="id,strategy_id,compiles,compile_error",
            filters={"id": f"eq.{body.strategy_version_id}"},
        )
        if not version:
            raise HTTPException(status_code=404, detail="no such strategy version")
        if version.get("compiles") is False:
            raise HTTPException(
                status_code=422,
                detail=f"that version does not compile: {version.get('compile_error')}",
            )
        payload["strategy_id"] = version["strategy_id"]

    try:
        created = db.insert("backtest_jobs", payload)
    except Exception as exc:
        # The quota is enforced by a database trigger, so surface it as a 429
        # rather than letting it look like a server fault.
        if "quota" in str(exc).lower():
            raise HTTPException(
                status_code=429,
                detail="you already have as many backtests queued or running as your limit allows",
            ) from exc
        raise

    return {"job": created[0]}


@router.get("/jobs")
async def list_jobs(db: UserDB, limit: int = 50) -> dict:
    return {
        "jobs": db.select(
            "backtest_jobs",
            columns="id,status,exchange,timeframe,pairs,timerange,builtin_strategy,"
                    "strategy_version_id,progress,progress_pct,stage,error,attempts,"
                    "created_at,started_at,finished_at",
            order="created_at.desc",
            limit=min(limit, 200),
        )
    }


@router.get("")
async def list_runs(db: UserDB, strategy_id: str | None = None, limit: int = 50) -> dict:
    filters = {"strategy_id": f"eq.{strategy_id}"} if strategy_id else {}
    return {
        "runs": db.select(
            "backtest_runs",
            columns="id,job_id,strategy_id,strategy_name,exchange,timeframe,pairs,"
                    "timerange_start,timerange_end,total_trades,wins,losses,win_rate,"
                    "profit_total_abs,profit_total_pct,profit_factor,expectancy,cagr,sharpe,"
                    "sortino,calmar,max_drawdown_abs,max_drawdown_pct,starting_balance,"
                    "final_balance,best_pair,worst_pair,trades_per_day,avg_trade_duration_min,"
                    "duration_seconds,created_at,"
                    # What was asked for, next to what ran. Without these the
                    # list cannot show that a decade-long request tested a month.
                    "requested_timerange,coverage_pct,coverage_note",
            filters=filters,
            order="created_at.desc",
            limit=min(limit, 200),
        )
    }


@router.get("/{run_id}")
async def get_run(run_id: str, db: UserDB) -> dict:
    run = db.select_one("backtest_runs", filters={"id": f"eq.{run_id}"})
    if not run:
        raise HTTPException(status_code=404, detail="no such backtest run")
    return {
        "run": run,
        "pairs": db.select(
            "backtest_pair_results",
            filters={"run_id": f"eq.{run_id}"},
            order="profit_abs.desc",
        ),
    }


@router.get("/{run_id}/trades")
async def get_trades(run_id: str, db: UserDB, limit: int = 500, offset: int = 0) -> dict:
    """Trades for a run, paged past PostgREST's ceiling when more are asked for.

    The distribution chart and the exit-reason breakdown are computed from this,
    so a truncated answer silently describes only the first part of the run.
    """
    wanted = max(1, min(limit, 20000))
    if wanted <= POSTGREST_PAGE and offset:
        rows = db.select("backtest_trades", filters={"run_id": f"eq.{run_id}"},
                         order="open_date.asc", limit=wanted, offset=offset)
    else:
        rows = _all_rows(db, "backtest_trades", columns="*",
                         filters={"run_id": f"eq.{run_id}"},
                         order="open_date.asc", cap=wanted + offset)[offset:]
    return {"trades": rows}


@router.get("/{run_id}/equity")
async def get_equity(run_id: str, db: UserDB) -> dict:
    """The whole curve, not the first thousand points of it."""
    return {
        "equity": _all_rows(
            db, "backtest_equity_curve",
            columns="at,balance,drawdown_abs,drawdown_pct",
            filters={"run_id": f"eq.{run_id}"},
            order="at.asc",
            cap=8000,
        )
    }


#: PostgREST answers at most this many rows per request whatever `limit` says.
#: Asking for 2000 quietly returns the first 1000, which is the worst kind of
#: wrong: an equity chart drawn from it looks complete and covers half the run.
POSTGREST_PAGE = 1000


def _all_rows(db, table: str, *, columns: str, filters: dict, order: str,
              cap: int = 20000) -> list[dict]:
    """Every row, paged past PostgREST's per-response ceiling."""
    out: list[dict] = []
    while len(out) < cap:
        rows = db.select(table, columns=columns, filters=filters, order=order,
                         limit=POSTGREST_PAGE, offset=len(out))
        out.extend(rows)
        if len(rows) < POSTGREST_PAGE:
            break
    return out[:cap]


def _all_trades(db, run_id: str) -> list[dict]:
    """Every trade in a run.

    Paging rather than truncating keeps the period totals correct -- a breakdown
    computed from a truncated set is wrong in a way that is very hard to notice.
    """
    return _all_rows(
        db, "backtest_trades",
        columns="pair,close_date,open_date,profit_abs,profit_ratio,stake_amount,"
                "trade_duration_min,exit_reason",
        filters={"run_id": f"eq.{run_id}"},
        order="close_date.asc",
    )


@router.get("/{run_id}/breakdown")
async def get_breakdown(run_id: str, db: UserDB, period: str = "month") -> dict:
    """Return per calendar period -- the answer to "was this steady or one month".

    Computed from the stored trades rather than from freqtrade's own report, so
    the period can be changed without re-running the backtest.
    """
    if period not in periods.PERIODS:
        raise HTTPException(
            status_code=422,
            detail=f"period must be one of: {', '.join(periods.PERIODS)}",
        )
    run = db.select_one(
        "backtest_runs",
        columns="id,starting_balance,stake_currency,timeframe",
        filters={"id": f"eq.{run_id}"},
    )
    if not run:
        raise HTTPException(status_code=404, detail="no such backtest run")

    trades = _all_trades(db, run_id)
    rows = periods.breakdown(
        trades, period=period,
        starting_balance=float(run.get("starting_balance") or 0) or None,
    )
    return {
        "period": period,
        "available": list(periods.PERIODS),
        "stake_currency": run.get("stake_currency"),
        "starting_balance": run.get("starting_balance"),
        "rows": rows,
        "summary": periods.summarise(rows),
    }


@router.get("/{run_id}/verdict")
async def get_verdict(run_id: str, db: UserDB) -> dict:
    """Whether this result is worth believing, separate from whether it is good."""
    run = db.select_one("backtest_runs", filters={"id": f"eq.{run_id}"})
    if not run:
        raise HTTPException(status_code=404, detail="no such backtest run")
    return verdict.assess(run, _all_trades(db, run_id)).as_dict()


@router.post("/jobs/{job_id}/cancel", status_code=200)
async def cancel_job(job_id: str, db: UserDB) -> dict:
    """Stop a job, whether it is waiting or already running.

    A running job used to be un-cancellable, on the grounds that nothing could
    reach the freqtrade process. Now the worker watches this status on its
    heartbeat and terminates the subprocess when it flips, so the honest answer
    changed.
    """
    job = db.select_one("backtest_jobs", columns="id,status", filters={"id": f"eq.{job_id}"})
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"job is already {job['status']}")

    db.update("backtest_jobs", {"status": "cancelled"}, filters={"id": f"eq.{job_id}"})
    return {
        "status": "cancelled",
        # Queued work stops immediately; a running one stops when the worker
        # next checks in, which is worth saying so nobody watches a live row for
        # a few seconds wondering whether the click registered.
        "detail": ("Stopped." if job["status"] == "queued"
                   else "Stopping — the worker checks in every few seconds."),
    }


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, db: UserDB) -> None:
    """Remove a finished job row. Cancel it first if it is still active."""
    job = db.select_one("backtest_jobs", columns="id,status", filters={"id": f"eq.{job_id}"})
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] in ("queued", "running"):
        raise HTTPException(
            status_code=409,
            detail="this job is still active. Cancel it first, then delete it.",
        )
    db.delete("backtest_jobs", filters={"id": f"eq.{job_id}"})


@router.delete("/failed", status_code=200)
async def delete_failed(db: UserDB) -> dict:
    """Clear out failed and cancelled jobs in one go.

    Exploratory work leaves a trail of them, and deleting one at a time is a
    chore that means the trail just stays there instead.
    """
    removed = db.delete("backtest_jobs", filters={"status": "in.(failed,cancelled)"})
    return {"deleted": len(removed)}


@router.delete("/{run_id}", status_code=204)
async def delete_run(run_id: str, db: UserDB) -> None:
    """Delete a result and everything under it.

    The trades, per-pair rows and equity curve go with it on the database's own
    cascade. The job that produced it goes too -- keeping it would leave a row
    in the queue pointing at a result that no longer exists.
    """
    run = db.select_one("backtest_runs", columns="id,job_id", filters={"id": f"eq.{run_id}"})
    if not run:
        raise HTTPException(status_code=404, detail="no such backtest run")

    db.delete("backtest_runs", filters={"id": f"eq.{run_id}"})
    if run.get("job_id"):
        try:
            db.delete("backtest_jobs", filters={"id": f"eq.{run['job_id']}"})
        except Exception as exc:  # noqa: BLE001 - the run is already gone
            log.info("run %s deleted; its job row remains: %s", run_id, exc)
