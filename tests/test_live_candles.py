"""The candle payload is trimmed before it leaves the app.

freqtrade returns every column the strategy computed. The chart reads eight of
them; on an indicator-heavy strategy the rest are several times the size of the
OHLCV they surround, and they cross the public internet for nothing.
"""

from __future__ import annotations

from app.api.routers.live import CHART_COLUMNS, _trim_columns


def _payload(columns, rows):
    return {"pair": "BTC/USDT", "timeframe": "5m", "columns": list(columns),
            "all_columns": list(columns), "data": [list(r) for r in rows],
            "length": len(rows)}


def test_indicator_columns_are_dropped():
    payload = _payload(
        ["date", "open", "high", "low", "close", "volume", "rsi", "ema_200",
         "bb_upper", "enter_long", "exit_long", "__date_ts"],
        [["2026-01-01", 1, 2, 0.5, 1.5, 10, 55.2, 1.1, 2.2, 0, 0, 1767225600000]],
    )
    original = dict(zip(payload["columns"], payload["data"][0]))
    out = _trim_columns(payload)
    assert out["columns"] == ["date", "open", "high", "low", "close", "volume",
                              "enter_long", "exit_long", "__date_ts"]
    assert out["data"] == [["2026-01-01", 1, 2, 0.5, 1.5, 10, 0, 0, 1767225600000]]
    # Every kept column keeps its own value: a mis-indexed trim would put the
    # RSI where the close belongs and the chart would draw it without complaint.
    for name, value in zip(out["columns"], out["data"][0]):
        assert value == original[name]
    # And the caller's payload is not edited underneath them.
    assert "rsi" in payload["columns"]


def test_what_the_strategy_computed_is_still_reported():
    payload = _payload(["date", "close", "rsi"], [["2026-01-01", 1.5, 55.2]])
    out = _trim_columns(payload)
    assert "rsi" in out["all_columns"], "the caller cannot tell what was dropped"


def test_a_payload_with_nothing_to_drop_is_untouched():
    payload = _payload(["date", "open", "high", "low", "close", "volume"],
                       [["2026-01-01", 1, 2, 0.5, 1.5, 10]])
    before = {"columns": list(payload["columns"]), "data": [list(r) for r in payload["data"]]}
    out = _trim_columns(payload)
    assert out["columns"] == before["columns"] and out["data"] == before["data"]


def test_an_unexpected_shape_is_passed_through_rather_than_mangled():
    for payload in ({"data": [1, 2, 3]}, {"columns": "not a list", "data": []}, {}):
        assert _trim_columns(dict(payload)) == payload


def test_both_signal_namings_survive():
    # freqtrade renamed buy/sell to enter_long/exit_long; old strategies still
    # emit the former and the chart reads whichever is present.
    assert {"buy", "sell", "enter_long", "exit_long"} <= set(CHART_COLUMNS)
