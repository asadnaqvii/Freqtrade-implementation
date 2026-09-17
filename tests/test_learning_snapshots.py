"""The snapshots: what a decision looked at, taken from what freqtrade holds.

The candle row is a pandas row, so these are exercised with real DataFrames;
the portfolio and risk builders are exercised with fakes that fail one piece
at a time, because one unreadable piece must never cost the record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from app.learning import snapshots


def a_frame(**overrides):
    row = {
        "date": pd.Timestamp("2026-09-17 04:00", tz="UTC"),
        "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000.0,
        "rsi": np.float64(61.2), "adx_falling": np.nan, "ema_ok": np.bool_(True),
        "enter_long": 1, "exit_long": 0, "enter_tag": "pullback",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_timeframes_are_read_as_seconds():
    assert snapshots.timeframe_seconds("4h") == 14400
    assert snapshots.timeframe_seconds("5m") == 300
    assert snapshots.timeframe_seconds("1d") == 86400
    with pytest.raises(ValueError):
        snapshots.timeframe_seconds("soon")


def test_the_last_row_of_a_dataframe_becomes_a_plain_dict():
    row = snapshots.last_row(a_frame())
    assert row["rsi"] == 61.2
    assert snapshots.last_row(pd.DataFrame()) is None
    assert snapshots.last_row(None) is None


def test_the_row_splits_into_candle_features_and_signals():
    market, features, signals = snapshots.split_row(snapshots.last_row(a_frame()), "4h")
    assert market == {
        "timeframe": "4h", "candle_open": "2026-09-17T04:00:00+00:00",
        "candle_close": "2026-09-17T08:00:00+00:00",
        "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 1000.0,
    }
    assert features == {"adx_falling": None, "ema_ok": True, "rsi": 61.2}
    assert signals == {"enter_long": 1, "exit_long": 0, "enter_tag": "pullback"}


def test_a_snapshot_is_capped_by_key_count_and_says_so():
    features = {f"f{i:03d}": float(i) for i in range(250)}
    capped = snapshots.cap_features(features)
    assert len(capped) == snapshots.MAX_FEATURE_KEYS + 1
    assert capped["_truncated"] == 50


def test_a_snapshot_is_capped_by_size_and_says_so():
    features = {f"blob{i}": "x" * 20_000 for i in range(5)}
    capped = snapshots.cap_features(features)
    assert "_truncated" in capped
    assert len(capped) < 6


def test_wants_entry_reads_the_row_the_way_freqtrade_does():
    assert snapshots.wants_entry({"enter_long": 1, "exit_long": 0}) == "enter_long"
    assert snapshots.wants_entry({"enter_long": 1, "exit_long": 1}) is None  # contradicted
    assert snapshots.wants_entry({"enter_long": 1, "enter_short": 1}) is None
    assert snapshots.wants_entry({"enter_short": 1}) == "enter_short"
    assert snapshots.wants_entry({"buy": 1}) == "enter_long"  # the old column names
    assert snapshots.wants_entry({"enter_long": np.nan}) is None
    assert snapshots.wants_entry({"enter_long": np.int64(1)}) == "enter_long"
    assert snapshots.wants_entry(None) is None


def test_wants_exit_follows_the_side():
    assert snapshots.wants_exit({"exit_long": 1}) is True
    assert snapshots.wants_exit({"exit_long": 1}, is_short=True) is False
    assert snapshots.wants_exit({"exit_short": 1}, is_short=True) is True


def test_an_outdated_candle_is_the_one_freqtrade_would_refuse():
    now = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
    assert snapshots.is_outdated(now - timedelta(hours=9), "4h", now) is True
    assert snapshots.is_outdated(now - timedelta(hours=5), "4h", now) is False


def test_a_time_floors_to_its_candle():
    when = datetime(2026, 9, 17, 6, 47, 12, tzinfo=timezone.utc)
    assert snapshots.floor_to_candle(when, "4h") == datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
    assert snapshots.candle_close(datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc), "4h") == \
        datetime(2026, 9, 17, 8, 0, tzinfo=timezone.utc)


class Wallets:
    def __init__(self, total=100.0, available=40.0, broken=False):
        self._total, self._available, self._broken = total, available, broken

    def get_total_stake_amount(self):
        return self._total

    def get_available_stake_amount(self):
        if self._broken:
            raise RuntimeError("wallet refresh failed")
        return self._available


class Trade:
    def __init__(self, id, pair, stake=10.0):
        self.id, self.pair, self.stake_amount = id, pair, stake
        self.amount, self.open_rate, self.is_short = 5.0, 2.0, False
        self.open_date_utc = datetime(2026, 9, 17, tzinfo=timezone.utc)
        self.enter_tag = "pullback"


def test_the_portfolio_snapshot_lists_positions_and_totals():
    snapshot = snapshots.portfolio_snapshot(
        wallets=Wallets(), open_trades=lambda: [Trade(1, "TRX/USDT"), Trade(2, "XRP/USDT", 12.0)],
        stake_currency="USDT",
    )
    assert snapshot["total_stake"] == 100.0
    assert snapshot["available_stake"] == 40.0
    assert snapshot["open_position_count"] == 2
    assert snapshot["stake_in_positions"] == 22.0
    assert snapshot["open_positions"][0]["pair"] == "TRX/USDT"
    assert snapshot["open_positions"][0]["open_date"] == "2026-09-17T00:00:00+00:00"


def test_one_unreadable_piece_does_not_cost_the_snapshot():
    snapshot = snapshots.portfolio_snapshot(wallets=Wallets(broken=True), open_trades=lambda: [],
                                            stake_currency="USDT")
    assert snapshot["total_stake"] == 100.0
    assert "available_stake" not in snapshot
    assert "wallet refresh failed" in snapshot["_unavailable"]["available_stake"]
    assert snapshot["open_position_count"] == 0


class Lock:
    pair, side, reason = "TRX/USDT", "*", "Cooldown period"
    lock_end_time_utc = datetime(2026, 9, 17, 16, 0, tzinfo=timezone.utc)


def test_the_risk_snapshot_names_the_locks_and_the_limits():
    snapshot = snapshots.risk_snapshot(
        pair_locks=lambda: [Lock()], global_lock=lambda: False, max_open_trades=6,
        free_slots=lambda: 2, stoploss=-0.06, trailing_stop=True, position_adjustment_enable=True,
        protections=[{"method": "StoplossGuard"}],
    )
    assert snapshot["pair_locks"] == [{"pair": "TRX/USDT", "side": "*", "reason": "Cooldown period",
                                       "lock_end_time": "2026-09-17T16:00:00+00:00"}]
    assert snapshot["global_lock"] is False
    assert snapshot["free_slots"] == 2
    assert snapshot["max_open_trades"] == 6
    assert snapshot["protections"] == [{"method": "StoplossGuard"}]
