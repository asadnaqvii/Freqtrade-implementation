"""Run a backtest with freqtrade's own backtesting engine.

Nothing here reimplements backtesting. The runner's job is to turn a queued job
row into a valid freqtrade config plus a strategy file on disk, shell out to
`freqtrade backtesting`, and hand the exported result to the parser.

Any exchange, any pairs, any quote currency, any past window: the job row
carries all four and none of them are constrained to a whitelist here. The only
limits are what the venue actually has history for, and that failure is reported
as itself rather than as a mystery.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from app.backtest.parser import BacktestExport

log = logging.getLogger(__name__)

# freqtrade accepts YYYYMMDD-YYYYMMDD, with either side optional.
TIMERANGE_RE = re.compile(r"^(\d{8})?-(\d{8})?$")
PAIR_RE = re.compile(r"^[A-Z0-9]{1,20}/[A-Z0-9]{1,20}$")
TIMEFRAME_RE = re.compile(r"^\d{1,3}[mhdw]$")


class BacktestError(RuntimeError):
    """The backtest could not be run, or freqtrade rejected it."""


@dataclass
class BacktestRequest:
    """One backtest, normalised from a backtest_jobs row."""

    strategy_name: str
    strategy_source: str | None = None       # generated code, when builder-authored
    strategy_path: str | None = None         # directory of built-in strategies
    exchange: str = "kucoin"
    timeframe: str = "5m"
    pairs: Sequence[str] = field(default_factory=list)
    timerange: str | None = None
    stake_currency: str = "USDT"
    stake_amount: float | str = "unlimited"
    starting_balance: float = 1000.0
    max_open_trades: int = 3
    fee: float | None = None
    enable_protections: bool = False
    download_data: bool = True
    extra_args: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not self.pairs:
            raise BacktestError("a backtest needs at least one pair")

        bad_pairs = [p for p in self.pairs if not PAIR_RE.match(p)]
        if bad_pairs:
            raise BacktestError(
                f"these do not look like trading pairs: {', '.join(bad_pairs)}. "
                "Use the venue's own symbol, e.g. BTC/USDT."
            )

        if not TIMEFRAME_RE.match(self.timeframe):
            raise BacktestError(
                f"timeframe {self.timeframe!r} is not valid; use e.g. 1m, 5m, 1h, 4h, 1d"
            )

        if self.timerange and not TIMERANGE_RE.match(self.timerange):
            raise BacktestError(
                f"timerange {self.timerange!r} must be YYYYMMDD-YYYYMMDD, and either "
                "side may be omitted (20240101- means 'since then')"
            )

        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", self.strategy_name):
            raise BacktestError(f"strategy name {self.strategy_name!r} is not a python identifier")

        if not re.match(r"^[a-z0-9_]+$", self.exchange):
            raise BacktestError(f"exchange {self.exchange!r} is not a valid ccxt id")

        # Quote currency must be consistent with the pairs, or freqtrade
        # silently backtests nothing and reports zero trades.
        quotes = {p.split("/")[1] for p in self.pairs}
        if self.stake_currency not in quotes:
            raise BacktestError(
                f"stake_currency is {self.stake_currency} but the pairs quote in "
                f"{', '.join(sorted(quotes))}. freqtrade would find no tradable pair "
                "and report an empty backtest."
            )

    @classmethod
    def from_job(cls, job: dict[str, Any], *, strategy_source: str | None = None,
                 strategy_name: str | None = None, strategy_path: str | None = None) -> "BacktestRequest":
        return cls(
            strategy_name=strategy_name or job.get("builtin_strategy") or "",
            strategy_source=strategy_source,
            strategy_path=strategy_path,
            exchange=job.get("exchange") or "kucoin",
            timeframe=job.get("timeframe") or "5m",
            pairs=list(job.get("pairs") or []),
            timerange=job.get("timerange"),
            stake_currency=job.get("stake_currency") or "USDT",
            stake_amount=job.get("stake_amount") or "unlimited",
            starting_balance=float(job.get("starting_balance") or 1000),
            max_open_trades=int(job.get("max_open_trades") or 3),
            fee=job.get("fee"),
            enable_protections=bool(job.get("enable_protections")),
            download_data=bool(job.get("download_data", True)),
            extra_args=job.get("extra_args") or {},
        )


@dataclass
class BacktestArtifacts:
    result_path: Path
    export: "BacktestExport"
    stdout: str
    duration_seconds: float
    freqtrade_version: str | None


def freqtrade_cmd() -> list[str]:
    """How to invoke freqtrade.

    Through the running interpreter rather than a bare `freqtrade` on PATH: the
    worker may run under a venv whose bin directory is not exported, and a
    PATH-resolved freqtrade could belong to a different environment than the one
    whose strategies and libraries we just used.
    """
    return [sys.executable, "-m", "freqtrade"]


def freqtrade_version() -> str | None:
    try:
        import freqtrade

        return freqtrade.__version__
    except Exception:  # pragma: no cover
        return None


def build_config(request: BacktestRequest, *, data_dir: Path, user_dir: Path) -> dict[str, Any]:
    """The smallest config freqtrade will accept for a backtest.

    Deliberately minimal: no API server, no credentials, dry run. A backtest must
    never be able to place an order, so the config it runs under does not contain
    anything that could.
    """
    config: dict[str, Any] = {
        "max_open_trades": request.max_open_trades,
        "stake_currency": request.stake_currency,
        "stake_amount": request.stake_amount,
        "tradable_balance_ratio": 0.99,
        "dry_run": True,
        "dry_run_wallet": request.starting_balance,
        "trading_mode": "spot",
        "margin_mode": "",
        "timeframe": request.timeframe,
        "exchange": {
            "name": request.exchange,
            # No key, no secret: a backtest reads public candle data only.
            "key": "",
            "secret": "",
            "pair_whitelist": list(request.pairs),
            "pair_blacklist": [],
            "ccxt_config": {},
            "ccxt_async_config": {"aiohttp_trust_env": True},
        },
        "pairlists": [{"method": "StaticPairList"}],
        "datadir": str(data_dir),
        "user_data_dir": str(user_dir),
        "entry_pricing": {"price_side": "same", "use_order_book": False, "order_book_top": 1},
        "exit_pricing": {"price_side": "same", "use_order_book": False, "order_book_top": 1},
        "internals": {"process_throttle_secs": 5},
        "enable_protections": request.enable_protections,
    }
    if request.fee is not None:
        config["fee"] = request.fee
    return config


def _run(cmd: list[str], *, cwd: Path, timeout: int, on_output: Callable[[str], None] | None = None) -> tuple[int, str]:
    log.info("running: %s", " ".join(cmd))
    env = dict(os.environ)
    # Keep freqtrade's own output unbuffered so progress lines arrive live.
    env["PYTHONUNBUFFERED"] = "1"

    try:
        process = subprocess.run(
            cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True, env=env, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise BacktestError(
            f"freqtrade did not finish within {timeout}s. Narrow the timerange or "
            "reduce the number of pairs."
        ) from exc

    output = (process.stdout or "") + (process.stderr or "")
    if on_output:
        on_output(output)
    return process.returncode, output


def _explain_failure(output: str, request: BacktestRequest) -> str:
    """Turn freqtrade's output into something a user can act on."""
    lowered = output.lower()

    if "no data found" in lowered or "no history data" in lowered:
        return (
            f"No candle data for {', '.join(request.pairs)} at {request.timeframe} "
            f"over {request.timerange or 'the requested window'}. Either the venue does "
            "not have history that far back, or the pair did not exist yet. Try a more "
            "recent timerange or a larger timeframe."
        )
    if "does not exist" in lowered and "strategy" in lowered:
        return (
            f"freqtrade could not find a strategy called {request.strategy_name}. "
            "If this is a builder strategy, the generated file did not reach the "
            "strategy directory."
        )
    if "exchange" in lowered and ("not supported" in lowered or "is not available" in lowered):
        return f"{request.exchange} is not usable through ccxt from this host."
    if "restricted location" in lowered or "restricted region" in lowered:
        return (
            f"{request.exchange} refused this host's requests, most likely a regional "
            "block. Candle downloads need to originate somewhere the venue serves."
        )
    if "insufficient" in lowered and "startup" in lowered:
        return (
            "The strategy asks for more warm-up candles than the timerange provides. "
            "Extend the timerange backwards."
        )

    if "could not load markets" in lowered or "reload_markets" in lowered:
        return (
            f"Could not load {request.exchange}'s market list. A backtest still needs "
            "the venue's symbol and precision data, so this host needs outbound access "
            "to the exchange API even though no order is placed."
        )

    tail = "\n".join(output.strip().splitlines()[-15:])
    return f"freqtrade exited with an error:\n{tail}"


def run_backtest(
    request: BacktestRequest,
    *,
    data_dir: str | Path,
    user_dir: str | Path,
    timeout_seconds: int = 3600,
    progress: Callable[[str], None] | None = None,
) -> BacktestArtifacts:
    """Download data if needed, run the backtest, return the parsed export."""
    request.validate()

    data_path = Path(data_dir)
    user_path = Path(user_dir)

    # Each run gets its own strategy and results directories. Sharing them means
    # two workers running concurrently can overwrite each other's strategy file
    # when two users happen to pick the same class name, and can pick up each
    # other's export when scanning the results directory afterwards. The candle
    # data directory stays shared -- that one is a cache, and sharing it is the
    # entire point.
    run_root = user_path / "runs" / uuid.uuid4().hex
    strategy_dir = run_root / "strategies"
    results_dir = run_root / "backtest_results"
    for directory in (data_path, strategy_dir, results_dir):
        directory.mkdir(parents=True, exist_ok=True)

    if request.strategy_source:
        target = strategy_dir / f"{request.strategy_name}.py"
        target.write_text(request.strategy_source, encoding="utf-8")
        log.info("wrote generated strategy to %s", target)
    elif request.strategy_path:
        source_file = Path(request.strategy_path) / f"{request.strategy_name}.py"
        if not source_file.exists():
            raise BacktestError(
                f"built-in strategy {request.strategy_name} not found at {source_file}"
            )
        shutil.copy2(source_file, strategy_dir / source_file.name)

    workdir = Path(tempfile.mkdtemp(prefix="backtest-"))
    try:
        config = build_config(request, data_dir=data_path, user_dir=user_path)
        config_file = workdir / "config.json"
        config_file.write_text(json.dumps(config, indent=2), encoding="utf-8")

        if request.download_data:
            if progress:
                progress(f"downloading {request.timeframe} candles for {len(request.pairs)} pair(s)")
            download_cmd = [
                *freqtrade_cmd(), "download-data",
                "--config", str(config_file),
                "--datadir", str(data_path),
                "--timeframes", request.timeframe,
                "--pairs", *request.pairs,
            ]
            if request.timerange:
                download_cmd += ["--timerange", request.timerange]

            code, output = _run(download_cmd, cwd=workdir, timeout=timeout_seconds)
            if code != 0:
                # A failed download is not always fatal: cached data may already
                # cover the window. Note it and let backtesting be the judge.
                log.warning("download-data exited %s; continuing on cached data", code)
                if progress:
                    progress("download failed; trying cached candles")

        export_file = results_dir / f"{request.strategy_name}.json"

        backtest_cmd = [
            *freqtrade_cmd(), "backtesting",
            "--config", str(config_file),
            "--strategy", request.strategy_name,
            "--strategy-path", str(strategy_dir),
            "--datadir", str(data_path),
            "--userdir", str(run_root),
            "--timeframe", request.timeframe,
            "--export", "trades",
            "--export-filename", str(export_file),
            "--cache", "none",
        ]
        if request.timerange:
            backtest_cmd += ["--timerange", request.timerange]

        if progress:
            progress("running freqtrade backtesting")

        started = datetime.now(timezone.utc)
        code, output = _run(backtest_cmd, cwd=workdir, timeout=timeout_seconds)
        duration = (datetime.now(timezone.utc) - started).total_seconds()

        if code != 0:
            raise BacktestError(_explain_failure(output, request))

        result_path = _locate_export(results_dir)
        if result_path is None:
            raise BacktestError(
                "freqtrade reported success but wrote no result file into "
                f"{results_dir}."
            )

        export = BacktestExport.from_path(result_path)

        return BacktestArtifacts(
            result_path=result_path,
            export=export,
            stdout=output,
            duration_seconds=duration,
            freqtrade_version=freqtrade_version(),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        # The export has already been read into memory by this point.
        shutil.rmtree(run_root, ignore_errors=True)


def _locate_export(results_dir: Path) -> Path | None:
    """Find the export freqtrade actually wrote.

    2026.x ignores the name given to --export-filename and writes its own
    `backtest-result-<timestamp>.zip`, so looking for the requested path finds
    nothing. The directory belongs to this run alone, so whatever is in it is
    ours -- no timestamp comparison needed to tell runs apart.
    """
    if not results_dir.exists():
        return None

    zips = list(results_dir.glob("*.zip"))
    if zips:
        return max(zips, key=lambda p: p.stat().st_mtime)

    jsons = [
        p for p in results_dir.glob("*.json")
        if not p.name.endswith(".meta.json") and not p.name.startswith(".")
    ]
    if jsons:
        return max(jsons, key=lambda p: p.stat().st_mtime)
    return None
