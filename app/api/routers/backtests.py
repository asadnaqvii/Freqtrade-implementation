"""Queue backtests and read their results."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from app.api.deps import CurrentUser, UserDB
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
                    "strategy_version_id,progress,error,attempts,created_at,started_at,finished_at",
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
                    "duration_seconds,created_at",
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
    return {
        "trades": db.select(
            "backtest_trades",
            filters={"run_id": f"eq.{run_id}"},
            order="open_date.asc",
            limit=min(limit, 2000),
            offset=offset,
        )
    }


@router.get("/{run_id}/equity")
async def get_equity(run_id: str, db: UserDB) -> dict:
    return {
        "equity": db.select(
            "backtest_equity_curve",
            columns="at,balance,drawdown_abs,drawdown_pct",
            filters={"run_id": f"eq.{run_id}"},
            order="at.asc",
            limit=2000,
        )
    }


@router.delete("/jobs/{job_id}", status_code=204)
async def cancel_job(job_id: str, db: UserDB) -> None:
    job = db.select_one("backtest_jobs", columns="id,status", filters={"id": f"eq.{job_id}"})
    if not job:
        raise HTTPException(status_code=404, detail="no such job")
    if job["status"] in ("completed", "failed", "cancelled"):
        raise HTTPException(status_code=409, detail=f"job is already {job['status']}")
    # A running job's worker will notice on its next heartbeat write.
    db.update("backtest_jobs", {"status": "cancelled"}, filters={"id": f"eq.{job_id}"})
