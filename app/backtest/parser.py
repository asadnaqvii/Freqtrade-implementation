"""Turn a freqtrade backtest export into rows for Supabase.

freqtrade 2026.x writes a zip containing the result JSON, the config it ran
under, a copy of the strategy source, and two feather files -- one of which is
the wallet series that gives us an equity curve for free.

Field names here were taken from a real export rather than from documentation,
because several of them are not what you would guess: `profit_total` is a ratio
while `profit_total_pct` (in the comparison block) is a percentage, drawdown is
reported as `max_drawdown_account`, and durations come as seconds with an `_s`
suffix alongside a human-readable twin.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

log = logging.getLogger(__name__)

# The wallet series has one row per candle per currency, which for a 90-day 5m
# backtest is ~50k rows. Storing all of them buys nothing a chart can show.
MAX_EQUITY_POINTS = 1500


class ParseError(RuntimeError):
    pass


def _dt(value: Any) -> str | None:
    """Normalise freqtrade's several datetime spellings to ISO-8601 UTC."""
    if value in (None, "", 0):
        return None
    if isinstance(value, (int, float)):
        # *_ts fields are epoch milliseconds.
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            # freqtrade writes backtest_start/end without a zone; they are UTC.
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat()
    return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    # Postgres numeric rejects NaN/Infinity from JSON; freqtrade emits both for
    # degenerate backtests (a single trade makes sortino infinite).
    if result != result or result in (float("inf"), float("-inf")):
        return None
    return result


def _pct(ratio: Any) -> float | None:
    """freqtrade reports most rates as ratios; the schema stores percentages."""
    value = _num(ratio)
    return None if value is None else value * 100.0


def _pair_key(entry: dict[str, Any]) -> str | None:
    key = entry.get("key")
    # The per-pair list ends with a TOTAL row that is a summary, not a pair.
    if not key or key == "TOTAL":
        return None
    return key


class BacktestExport:
    """A parsed freqtrade export, ready to become database rows."""

    def __init__(self, result: dict[str, Any], *, wallet: Any = None,
                 config: dict[str, Any] | None = None, source: str | None = None) -> None:
        strategies = result.get("strategy") or {}
        if not strategies:
            raise ParseError("export contains no 'strategy' block")

        self.strategy_name = next(iter(strategies))
        self.metrics: dict[str, Any] = strategies[self.strategy_name]
        self.comparison = result.get("strategy_comparison") or []
        self.wallet = wallet
        self.config = config or {}
        self.strategy_source = source

    # -- loading -----------------------------------------------------------
    @classmethod
    def from_path(cls, path: str | Path) -> "BacktestExport":
        path = Path(path)
        if path.suffix == ".zip":
            return cls.from_zip(path)
        with path.open(encoding="utf-8") as handle:
            return cls(json.load(handle))

    @classmethod
    def from_zip(cls, path: str | Path) -> "BacktestExport":
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()

            main = next(
                (n for n in names
                 if n.endswith(".json")
                 and not n.endswith("_config.json")
                 and not n.endswith(".meta.json")),
                None,
            )
            if main is None:
                raise ParseError(f"no result json inside {path}")
            result = json.loads(archive.read(main))

            config = None
            config_name = next((n for n in names if n.endswith("_config.json")), None)
            if config_name:
                try:
                    config = json.loads(archive.read(config_name))
                except json.JSONDecodeError:
                    config = None

            source = None
            py_name = next((n for n in names if n.endswith(".py")), None)
            if py_name:
                source = archive.read(py_name).decode("utf-8", errors="replace")

            wallet = None
            wallet_name = next((n for n in names if n.endswith("_wallet.feather")), None)
            if wallet_name:
                try:
                    import pandas as pd

                    wallet = pd.read_feather(io.BytesIO(archive.read(wallet_name)))
                except Exception as exc:  # equity curve is a nice-to-have
                    log.info("could not read the wallet series: %s", exc)

        return cls(result, wallet=wallet, config=config, source=source)

    # -- projections -------------------------------------------------------
    def run_row(self, **extra: Any) -> dict[str, Any]:
        """The backtest_runs row."""
        m = self.metrics
        best = m.get("best_pair") or {}
        worst = m.get("worst_pair") or {}
        holding_seconds = _num(m.get("holding_avg_s"))

        row: dict[str, Any] = {
            "strategy_name": m.get("strategy_name") or self.strategy_name,
            "timeframe": m.get("timeframe"),
            "pairs": list(m.get("pairlist") or []),
            "stake_currency": m.get("stake_currency") or "USDT",
            "starting_balance": _num(m.get("starting_balance")),
            "final_balance": _num(m.get("final_balance")),
            "max_open_trades": m.get("max_open_trades"),

            "timerange_start": _dt(m.get("backtest_start")),
            "timerange_end": _dt(m.get("backtest_end")),

            "total_trades": int(m.get("total_trades") or 0),
            "wins": int(m.get("wins") or 0),
            "losses": int(m.get("losses") or 0),
            "draws": int(m.get("draws") or 0),
            "win_rate": _num(m.get("winrate")),

            "profit_total_abs": _num(m.get("profit_total_abs")),
            "profit_total_pct": _pct(m.get("profit_total")),
            "profit_factor": _num(m.get("profit_factor")),
            "expectancy": _num(m.get("expectancy")),
            "expectancy_ratio": _num(m.get("expectancy_ratio")),
            "cagr": _num(m.get("cagr")),
            "sharpe": _num(m.get("sharpe")),
            "sortino": _num(m.get("sortino")),
            "calmar": _num(m.get("calmar")),

            "max_drawdown_abs": _num(m.get("max_drawdown_abs")),
            # freqtrade renamed this over time; account is the current spelling.
            "max_drawdown_pct": _pct(
                m.get("max_drawdown_account", m.get("max_relative_drawdown"))
            ),
            "max_drawdown_start": _dt(m.get("drawdown_start")),
            "max_drawdown_end": _dt(m.get("drawdown_end")),

            "avg_trade_duration_min": holding_seconds / 60.0 if holding_seconds else None,
            "best_pair": _pair_key(best),
            "worst_pair": _pair_key(worst),
            "trades_per_day": _num(m.get("trades_per_day")),

            # Keep the whole block: the schema cannot anticipate every metric
            # freqtrade adds, and losing them means re-running to get them back.
            "raw_metrics": self._raw_metrics(),
        }
        row.update(extra)
        return row

    def _raw_metrics(self) -> dict[str, Any]:
        """Metrics minus the bulky per-trade lists already stored relationally."""
        skip = {"trades", "results_per_pair", "results_per_enter_tag",
                "results_per_exit_reason", "exit_reason_summary", "mix_tag_stats",
                "periodic_breakdown", "daily_profit", "locks", "wallet_stats"}
        return {k: v for k, v in self.metrics.items() if k not in skip}

    def pair_rows(self, run_id: str) -> list[dict[str, Any]]:
        rows = []
        for entry in self.metrics.get("results_per_pair") or []:
            pair = _pair_key(entry)
            if pair is None:
                continue
            duration = entry.get("duration_avg")
            rows.append({
                "run_id": run_id,
                "pair": pair,
                "trades": int(entry.get("trades") or 0),
                "wins": int(entry.get("wins") or 0),
                "losses": int(entry.get("losses") or 0),
                "draws": int(entry.get("draws") or 0),
                "profit_abs": _num(entry.get("profit_total_abs")),
                "profit_pct": _num(entry.get("profit_total_pct")),
                "profit_mean_pct": _num(entry.get("profit_mean_pct")),
                "profit_sum_pct": _num(entry.get("profit_sum_pct")),
                "duration_avg_min": _duration_to_minutes(duration),
            })
        return rows

    def trade_rows(self, run_id: str) -> Iterator[dict[str, Any]]:
        for trade in self.metrics.get("trades") or []:
            yield {
                "run_id": run_id,
                "pair": trade.get("pair"),
                "is_short": bool(trade.get("is_short")),
                "open_date": _dt(trade.get("open_date")),
                "close_date": _dt(trade.get("close_date")),
                "open_rate": _num(trade.get("open_rate")),
                "close_rate": _num(trade.get("close_rate")),
                "amount": _num(trade.get("amount")),
                "stake_amount": _num(trade.get("stake_amount")),
                "profit_abs": _num(trade.get("profit_abs")),
                "profit_ratio": _num(trade.get("profit_ratio")),
                "trade_duration_min": int(trade.get("trade_duration") or 0),
                "enter_tag": (trade.get("enter_tag") or None),
                "exit_reason": trade.get("exit_reason"),
                "fee_open": _num(trade.get("fee_open")),
                "fee_close": _num(trade.get("fee_close")),
            }

    def equity_rows(self, run_id: str, *, max_points: int = MAX_EQUITY_POINTS) -> list[dict[str, Any]]:
        """Downsample the wallet series into an equity curve.

        Keeps the running peak and drawdown alongside the balance so the chart
        does not have to recompute them, and always keeps the deepest point --
        losing the trough to downsampling would understate the drawdown.
        """
        if self.wallet is None or len(self.wallet) == 0:
            return []

        try:
            frame = self.wallet
            if "total_quote" not in frame.columns or "date" not in frame.columns:
                return []

            series = frame[["date", "total_quote"]].dropna()
            if series.empty:
                return []

            peak = series["total_quote"].cummax()
            drawdown_abs = peak - series["total_quote"]
            trough_index = int(drawdown_abs.values.argmax())

            step = max(1, len(series) // max_points)
            keep = set(range(0, len(series), step))
            keep.add(0)
            keep.add(len(series) - 1)
            keep.add(trough_index)

            rows = []
            for position in sorted(keep):
                timestamp = series["date"].iloc[position]
                balance = _num(series["total_quote"].iloc[position])
                high_water = _num(peak.iloc[position]) or 0.0
                down = _num(drawdown_abs.iloc[position]) or 0.0
                rows.append({
                    "run_id": run_id,
                    "at": timestamp.isoformat() if hasattr(timestamp, "isoformat") else str(timestamp),
                    "balance": balance,
                    "drawdown_abs": down,
                    "drawdown_pct": (down / high_water * 100.0) if high_water else 0.0,
                })
            return rows
        except Exception as exc:  # pragma: no cover - the curve is optional
            log.info("could not build an equity curve: %s", exc)
            return []


def _duration_to_minutes(value: Any) -> float | None:
    """'14:30:00' or '1 day, 2:00:00' -> minutes."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text or text == "0:00":
        return 0.0

    days = 0
    if "day" in text:
        head, _, text = text.partition(",")
        try:
            days = int(head.split()[0])
        except (ValueError, IndexError):
            days = 0
        text = text.strip()

    parts = text.split(":")
    try:
        numbers = [float(p) for p in parts]
    except ValueError:
        return None

    if len(numbers) == 3:
        hours, minutes, seconds = numbers
    elif len(numbers) == 2:
        hours, minutes, seconds = 0.0, numbers[0], numbers[1]
    else:
        return None

    return days * 1440 + hours * 60 + minutes + seconds / 60.0
