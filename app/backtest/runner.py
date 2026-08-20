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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from app.backtest.parser import BacktestExport

log = logging.getLogger(__name__)

#: How many times to ask for older candles before accepting that the history has
#: run out. Each pass costs a few seconds and only continues while it is still
#: reaching further back, so this is a backstop, not a budget.
MAX_PREPEND_PASSES = 12

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


#: Candles of warm-up to assume when the strategy does not say. Comfortably above
#: the usual 200-period moving average, and the data is cached, so being generous
#: here costs one download rather than a failed run.
DEFAULT_STARTUP_CANDLES = 400

#: Extra candles on top of the strategy's own requirement. freqtrade drops
#: partial candles at the edges, so landing exactly on the boundary still fails.
STARTUP_MARGIN_CANDLES = 40

# Matches both `startup_candle_count = 400` and the annotated form freqtrade's
# own strategies use, `startup_candle_count: int = 400`. Missing the annotation
# meant silently falling back to the default -- which happened to be right often
# enough to hide the bug.
_STARTUP_RE = re.compile(r"startup_candle_count\s*(?::\s*[A-Za-z_][\w\[\], ]*\s*)?=\s*(\d+)")

_TIMEFRAME_MINUTES = {"m": 1, "h": 60, "d": 1440, "w": 10080, "M": 43200}


def timeframe_minutes(timeframe: str) -> int | None:
    if not timeframe or timeframe[-1] not in _TIMEFRAME_MINUTES:
        return None
    try:
        return int(timeframe[:-1]) * _TIMEFRAME_MINUTES[timeframe[-1]]
    except ValueError:
        return None


def startup_candles(strategy_source: str | None) -> int:
    """How many candles the strategy needs before it can produce a signal."""
    if not strategy_source:
        return DEFAULT_STARTUP_CANDLES
    found = _STARTUP_RE.search(strategy_source)
    if not found:
        return DEFAULT_STARTUP_CANDLES
    try:
        return max(int(found.group(1)), 0)
    except ValueError:
        return DEFAULT_STARTUP_CANDLES


def download_timerange(request: "BacktestRequest") -> str | None:
    """The window to DOWNLOAD, which is wider than the window to test.

    freqtrade needs startup_candle_count candles before the first one it can
    trade, so it shifts the backtest start forward by that many. Download only
    the requested window and it shifts past the end: "no data left after
    adjusting for startup candles", and a request for exactly 2022 returns
    nothing at all.

    So the download reaches further back while `backtesting` still receives the
    user's own timerange -- their dates stay the dates that are tested, and the
    warm-up happens on candles just outside them.
    """
    if not request.timerange:
        return None
    start, end = _requested_bounds(request.timerange)
    minutes = timeframe_minutes(request.timeframe)
    if start is None or minutes is None:
        return request.timerange

    needed = startup_candles(request.strategy_source) + STARTUP_MARGIN_CANDLES
    padded = start - timedelta(minutes=needed * minutes)
    tail = request.timerange.partition("-")[2]
    return f"{padded:%Y%m%d}-{tail}"


def _requested_bounds(timerange: str | None) -> tuple[datetime | None, datetime | None]:
    """freqtrade's YYYYMMDD-YYYYMMDD, either side optionally blank."""
    if not timerange or "-" not in timerange:
        return None, None
    start, _, end = timerange.partition("-")

    def one(part: str) -> datetime | None:
        part = part.strip()
        if len(part) != 8 or not part.isdigit():
            return None
        try:
            return datetime.strptime(part, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return one(start), one(end)


def _cached_start(data_dir: Path, pairs: list[str], timeframe: str) -> datetime | None:
    """The oldest candle currently on disk across these pairs.

    A backtest spans its pairs together, so the newest of the per-pair starts is
    what actually bounds the window. Used to tell whether another prepend pass
    achieved anything -- when this could not find the files it always answered
    None, the loop concluded "no progress" and gave up after one pass.
    """
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover - pandas is a hard dependency here
        return None

    folder = data_dir
    starts: list[datetime] = []
    for pair in pairs:
        stem = pair.replace("/", "_").replace(":", "_")
        found = None
        for suffix in (".feather", ".json", ".json.gz", ".parquet"):
            candidate = folder / f"{stem}-{timeframe}{suffix}"
            if candidate.exists():
                found = candidate
                break
        if found is None:
            return None
        try:
            frame = pd.read_feather(found) if found.suffix == ".feather" else None
            if frame is None or "date" not in frame.columns or frame.empty:
                return None
            starts.append(pd.to_datetime(frame["date"].iloc[0], utc=True).to_pydatetime())
        except Exception as exc:  # noqa: BLE001 - an unreadable cache is just unknown
            log.info("could not read cached candles at %s: %s", found, exc)
            return None
    return max(starts) if starts else None


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

    # One cache directory per exchange, and this is not cosmetic. freqtrade's
    # create_datadir only appends the exchange name when it is choosing the
    # directory itself; passing --datadir explicitly makes it use that path
    # verbatim. So every venue's candles were landing in one flat folder under
    # the same name -- BTC_USDT-1d.feather written by KuCoin, then appended to
    # and read back by a Binance run. A backtest "on Binance" could be scoring a
    # strategy against KuCoin's prices without anything saying so.
    data_path = Path(data_dir) / request.exchange.lower()
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
            base_cmd = [
                *freqtrade_cmd(), "download-data",
                "--config", str(config_file),
                "--datadir", str(data_path),
                "--timeframes", request.timeframe,
                "--pairs", *request.pairs,
            ]
            # Wider than what gets tested: see download_timerange.
            fetch_range = download_timerange(request)
            timerange_args = ["--timerange", fetch_range] if fetch_range else []

            # Two passes, and the first one is the whole point. freqtrade's
            # download-data only ever APPENDS to what is already cached -- it
            # will not fetch candles older than the oldest one on disk unless
            # told to prepend. So once a short run had cached a month of 5m
            # candles, every later request for five or ten years found data
            # present, downloaded nothing, and quietly backtested that month.
            # The result reported success over 29 days having been asked for a
            # decade, which is the worst possible way to be wrong.
            want_start, _ = _requested_bounds(fetch_range)

            # Establish the cache first. --prepend extends an existing dataset
            # backwards; with nothing on disk it does nothing at all, which is
            # why the loop below used to exit after a single fruitless pass.
            if progress:
                progress("fetching candles")
            code, output = _run(base_cmd + timerange_args, cwd=workdir, timeout=timeout_seconds)
            if code != 0:
                log.warning("download-data exited %s: %s", code, output[-400:])
                if progress:
                    progress("download failed; continuing on cached candles")

            # Prepend repeatedly rather than once. A single pass reaches back
            # only so far -- exchanges cap candles per request and freqtrade
            # does not loop indefinitely -- which is why asking for six years
            # produced five and a half and looked like the venue's limit. Keep
            # going while each pass actually moves the start earlier, and stop
            # the moment it does not: that is the real end of the history.
            if request.timerange:
                previous = _cached_start(data_path, request.pairs, request.timeframe)
                for attempt in range(1, MAX_PREPEND_PASSES + 1):
                    if want_start and previous and previous <= want_start:
                        break
                    if progress:
                        reached = previous.date().isoformat() if previous else "…"
                        progress(f"extending history backwards (pass {attempt}, "
                                 f"have from {reached})")
                    code, output = _run(base_cmd + timerange_args + ["--prepend"],
                                        cwd=workdir, timeout=timeout_seconds)
                    if code != 0:
                        log.warning("download-data --prepend exited %s: %s",
                                    code, output[-400:])
                        break
                    current = _cached_start(data_path, request.pairs, request.timeframe)
                    if current is None or (previous is not None and current >= previous):
                        # No further back than last time: this is the venue's
                        # earliest candle, not a cap we can push through.
                        previous = current or previous
                        break
                    previous = current



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
