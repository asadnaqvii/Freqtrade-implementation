"""Tests for the backtest request validation and result parser.

The parser is written against a real freqtrade 2026.7 export, so the fixtures
here use the field names and shapes that export actually contains -- `winrate`
not `win_rate`, `profit_total` as a ratio, `max_drawdown_account`, durations as
`*_s` seconds.
"""

from __future__ import annotations

import json
import zipfile

import pytest

from app.backtest.parser import BacktestExport, ParseError, _duration_to_minutes
from app.backtest.runner import BacktestError, BacktestRequest, build_config


# ---------------------------------------------------------------------------
# Request validation
# ---------------------------------------------------------------------------

def make_request(**kw) -> BacktestRequest:
    defaults = dict(strategy_name="Demo", pairs=["BTC/USDT"], stake_currency="USDT")
    defaults.update(kw)
    return BacktestRequest(**defaults)


def test_a_valid_request_passes():
    make_request(timerange="20240101-20240201").validate()


def test_no_pairs_is_rejected():
    with pytest.raises(BacktestError, match="at least one pair"):
        make_request(pairs=[]).validate()


@pytest.mark.parametrize("pair", ["BTCUSDT", "btc/usdt", "BTC-USDT", "BTC/USDT/PERP", "'; drop"])
def test_malformed_pairs_are_rejected(pair):
    with pytest.raises(BacktestError, match="trading pairs"):
        make_request(pairs=[pair]).validate()


@pytest.mark.parametrize("timeframe", ["5", "5x", "abc", "5 m", ""])
def test_malformed_timeframes_are_rejected(timeframe):
    with pytest.raises(BacktestError, match="timeframe"):
        make_request(timeframe=timeframe).validate()


@pytest.mark.parametrize("timerange", ["2024-01-01", "20240101", "abc-def", "20240101-20240201-x"])
def test_malformed_timeranges_are_rejected(timerange):
    with pytest.raises(BacktestError, match="timerange"):
        make_request(timerange=timerange).validate()


@pytest.mark.parametrize("timerange", ["20240101-20240201", "20240101-", "-20240201", "-"])
def test_open_ended_timeranges_are_accepted(timerange):
    make_request(timerange=timerange).validate()


def test_quote_currency_must_match_the_pairs():
    # Otherwise freqtrade finds no tradable pair and reports an empty backtest
    # rather than an error, which is a miserable thing to debug.
    with pytest.raises(BacktestError, match="stake_currency"):
        make_request(pairs=["BTC/USDT"], stake_currency="USDC").validate()


def test_any_exchange_and_any_quote_currency_are_allowed():
    make_request(exchange="binance", pairs=["ETH/BTC"], stake_currency="BTC").validate()
    make_request(exchange="kraken", pairs=["XRP/EUR"], stake_currency="EUR").validate()


def test_strategy_name_must_be_an_identifier():
    with pytest.raises(BacktestError, match="python identifier"):
        make_request(strategy_name="not a name; import os").validate()


def test_exchange_id_is_constrained():
    with pytest.raises(BacktestError, match="ccxt id"):
        make_request(exchange="../../etc/passwd").validate()


def test_generated_config_cannot_place_an_order(tmp_path):
    config = build_config(make_request(), data_dir=tmp_path, user_dir=tmp_path)
    assert config["dry_run"] is True
    assert config["exchange"]["key"] == ""
    assert config["exchange"]["secret"] == ""
    # No api_server block at all: a backtest has no business exposing one.
    assert "api_server" not in config


def test_config_carries_the_requested_window_and_pairs(tmp_path):
    request = make_request(pairs=["BTC/USDT", "ETH/USDT"], exchange="binance", timeframe="1h")
    config = build_config(request, data_dir=tmp_path, user_dir=tmp_path)
    assert config["exchange"]["name"] == "binance"
    assert config["exchange"]["pair_whitelist"] == ["BTC/USDT", "ETH/USDT"]
    assert config["timeframe"] == "1h"


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

METRICS = {
    "strategy_name": "Demo",
    "timeframe": "5m",
    "pairlist": ["BTC/USDT", "ETH/USDT"],
    "stake_currency": "USDT",
    "starting_balance": 1000,
    "final_balance": 1250.5,
    "max_open_trades": 3,
    "backtest_start": "2024-01-10 00:00:00",
    "backtest_end": "2024-04-10 00:00:00",
    "total_trades": 140,
    "wins": 80, "losses": 55, "draws": 5,
    "winrate": 0.5714,
    "profit_total": 0.2505,
    "profit_total_abs": 250.5,
    "profit_factor": 1.4,
    "expectancy": 1.79,
    "expectancy_ratio": 0.21,
    "cagr": 1.62,
    "sharpe": 2.1, "sortino": 3.4, "calmar": 5.1,
    "max_drawdown_abs": 120.25,
    "max_drawdown_account": 0.1103,
    "drawdown_start": "2024-02-01 10:00:00",
    "drawdown_end": "2024-02-11 04:00:00",
    "holding_avg_s": 50880.0,
    "best_pair": {"key": "BTC/USDT", "profit_total_abs": 180.0},
    "worst_pair": {"key": "ETH/USDT", "profit_total_abs": 70.5},
    "trades_per_day": 1.54,
    "results_per_pair": [
        {"key": "BTC/USDT", "trades": 70, "wins": 45, "losses": 23, "draws": 2,
         "profit_total_abs": 180.0, "profit_total_pct": 18.0, "profit_mean_pct": 0.26,
         "duration_avg": "10:44:00"},
        {"key": "ETH/USDT", "trades": 70, "wins": 35, "losses": 32, "draws": 3,
         "profit_total_abs": 70.5, "profit_total_pct": 7.05, "profit_mean_pct": 0.1,
         "duration_avg": "1 day, 2:00:00"},
        # freqtrade appends a TOTAL summary row that is not a pair.
        {"key": "TOTAL", "trades": 140, "wins": 80, "losses": 55, "draws": 5,
         "profit_total_abs": 250.5, "profit_total_pct": 25.05},
    ],
    "trades": [
        {"pair": "BTC/USDT", "is_short": False,
         "open_date": "2024-01-10 12:25:00+00:00", "close_date": "2024-01-11 00:20:00+00:00",
         "open_rate": 222.9, "close_rate": 230.1, "amount": 0.44, "stake_amount": 100.0,
         "profit_abs": 3.2, "profit_ratio": 0.032, "trade_duration": 715,
         "enter_tag": "entry_long", "exit_reason": "roi", "fee_open": 0.001, "fee_close": 0.001},
    ],
}

RESULT = {"strategy": {"Demo": METRICS}, "strategy_comparison": []}


def test_export_with_no_strategy_block_is_rejected():
    with pytest.raises(ParseError, match="no 'strategy' block"):
        BacktestExport({"strategy": {}})


def test_run_row_maps_the_headline_metrics():
    row = BacktestExport(RESULT).run_row()
    assert row["strategy_name"] == "Demo"
    assert row["total_trades"] == 140
    assert row["win_rate"] == pytest.approx(0.5714)
    # profit_total is a ratio in the export; the column stores a percentage.
    assert row["profit_total_pct"] == pytest.approx(25.05)
    assert row["profit_total_abs"] == pytest.approx(250.5)
    assert row["max_drawdown_pct"] == pytest.approx(11.03)
    assert row["max_drawdown_abs"] == pytest.approx(120.25)
    assert row["avg_trade_duration_min"] == pytest.approx(848.0)
    assert row["best_pair"] == "BTC/USDT"
    assert row["worst_pair"] == "ETH/USDT"


def test_naive_backtest_dates_are_treated_as_utc():
    row = BacktestExport(RESULT).run_row()
    assert row["timerange_start"] == "2024-01-10T00:00:00+00:00"
    assert row["timerange_end"] == "2024-04-10T00:00:00+00:00"


def test_total_row_is_not_mistaken_for_a_pair():
    rows = BacktestExport(RESULT).pair_rows("run-1")
    assert {r["pair"] for r in rows} == {"BTC/USDT", "ETH/USDT"}


def test_pair_durations_handle_the_day_form():
    rows = {r["pair"]: r for r in BacktestExport(RESULT).pair_rows("run-1")}
    assert rows["BTC/USDT"]["duration_avg_min"] == pytest.approx(644.0)
    assert rows["ETH/USDT"]["duration_avg_min"] == pytest.approx(1560.0)


def test_trade_rows_carry_the_run_id_and_parse_dates():
    trades = list(BacktestExport(RESULT).trade_rows("run-1"))
    assert len(trades) == 1
    assert trades[0]["run_id"] == "run-1"
    assert trades[0]["open_date"].startswith("2024-01-10T12:25")
    assert trades[0]["exit_reason"] == "roi"


def test_non_finite_metrics_become_null():
    """A degenerate backtest emits NaN and Infinity, which Postgres numeric rejects."""
    metrics = dict(METRICS, sortino=float("inf"), sharpe=float("nan"), calmar=float("-inf"))
    row = BacktestExport({"strategy": {"Demo": metrics}}).run_row()
    assert row["sortino"] is None
    assert row["sharpe"] is None
    assert row["calmar"] is None


def test_raw_metrics_drops_the_bulky_lists_but_keeps_the_rest():
    row = BacktestExport(RESULT).run_row()
    raw = row["raw_metrics"]
    assert "trades" not in raw
    assert "results_per_pair" not in raw
    assert raw["profit_factor"] == 1.4


def test_extra_fields_survive_in_raw_metrics():
    # freqtrade adds metrics over time; losing them means re-running to get them.
    metrics = dict(METRICS, some_future_metric=42)
    row = BacktestExport({"strategy": {"Demo": metrics}}).run_row()
    assert row["raw_metrics"]["some_future_metric"] == 42


def test_run_row_accepts_caller_supplied_columns():
    row = BacktestExport(RESULT).run_row(owner_id="abc", exchange="kraken")
    assert row["owner_id"] == "abc"
    assert row["exchange"] == "kraken"


def test_reads_a_zip_export(tmp_path):
    """freqtrade 2026.x writes a zip, not the json filename it was given."""
    archive = tmp_path / "backtest-result-2026-01-01_00-00-00.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("backtest-result.json", json.dumps(RESULT))
        zf.writestr("backtest-result_config.json", json.dumps({"max_open_trades": 3}))
        zf.writestr("backtest-result_Demo.py", "class Demo: pass")

    export = BacktestExport.from_path(archive)
    assert export.run_row()["total_trades"] == 140
    assert export.config["max_open_trades"] == 3
    # The strategy source travels with the result, so a run is always traceable.
    assert "class Demo" in export.strategy_source


def test_equity_curve_is_empty_without_a_wallet_series():
    assert BacktestExport(RESULT).equity_rows("run-1") == []


def test_equity_curve_downsamples_but_keeps_the_deepest_drawdown():
    pd = pytest.importorskip("pandas")
    n = 6000
    balances = [1000.0] * n
    balances[3000] = 100.0  # a single catastrophic trough
    wallet = pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=n, freq="5min", tz="UTC"),
        "total_quote": balances,
    })

    rows = BacktestExport(RESULT, wallet=wallet).equity_rows("run-1", max_points=100)
    assert len(rows) <= 105
    # Downsampling that loses the trough would understate the drawdown.
    assert min(r["balance"] for r in rows) == 100.0
    assert max(r["drawdown_pct"] for r in rows) == pytest.approx(90.0)


@pytest.mark.parametrize("value,expected", [
    ("14:30:00", 870.0),
    ("0:05:00", 5.0),
    ("1 day, 2:00:00", 1560.0),
    ("2 days, 0:30:00", 2910.0),
    ("0:00", 0.0),
    (None, None),
    ("nonsense", None),
])
def test_duration_parsing(value, expected):
    assert _duration_to_minutes(value) == expected


# ---------------------------------------------------------------------------
# Equity curve: one row per currency per candle
# ---------------------------------------------------------------------------

def test_equity_curve_sums_currencies_instead_of_colliding_on_timestamp():
    """The wallet feather has a row per currency per candle.

    total_quote is that one currency's value, not the account total. Taking rows
    as they came emitted several rows sharing a timestamp -- which violates the
    (run_id, at) unique index and killed the run at the final write -- and each
    surviving point showed one currency's holding rather than the equity, which
    also made the drawdown wrong.
    """
    import pandas as pd

    from app.backtest.parser import BacktestExport

    stamps = pd.date_range("2024-01-01", periods=4, freq="h", tz="UTC")
    wallet = pd.DataFrame({
        "date": list(stamps) * 3,
        "currency": ["USDT"] * 4 + ["BTC"] * 4 + ["ETH"] * 4,
        "total_quote": [600.0, 500.0, 400.0, 700.0]
                       + [300.0, 300.0, 200.0, 250.0]
                       + [100.0, 100.0, 100.0, 100.0],
    })
    export = BacktestExport({"strategy": {"S": {}}}, wallet=wallet)
    rows = export.equity_rows("run-1")

    ats = [r["at"] for r in rows]
    assert len(ats) == len(set(ats)), "duplicate timestamps would break the unique index"
    assert len(rows) == 4
    # Account equity is the sum across currencies, not any single one.
    assert [r["balance"] for r in rows] == [1000.0, 900.0, 700.0, 1050.0]
    # Deepest point is 1000 -> 700, so 300 and 30%.
    worst = max(rows, key=lambda r: r["drawdown_abs"])
    assert worst["drawdown_abs"] == 300.0
    assert worst["drawdown_pct"] == pytest.approx(30.0)


def test_equity_curve_survives_a_wallet_with_nothing_in_it():
    import pandas as pd

    from app.backtest.parser import BacktestExport

    empty = pd.DataFrame({"date": [], "total_quote": []})
    assert BacktestExport({"strategy": {"S": {}}}, wallet=empty).equity_rows("run-1") == []
