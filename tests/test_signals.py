"""Capturing what the strategy said.

The third record in the chain, and the one that was never written down:

    the strategy said  ->  the bot instructed  ->  the exchange filled

Only the last two were ever compared, so a signal the bot could not act on --
no free slot, no stake, pair locked, bot mid-deploy -- left no trace anywhere.
A strategy that cannot trade looked exactly like a strategy with nothing to say.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.validation import signals

BAR = 1755600000000        # epoch ms


def payload(columns, data, **kw):
    return {"pair": "BTC/USDT", "timeframe": "5m", "strategy": "TrendPullback",
            "columns": columns, "data": data, **kw}


NOW = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)


def test_an_entry_signal_is_captured_with_the_bar_it_belongs_to():
    out = signals.extract(payload(
        ["__date_ts", "close", "enter_long", "exit_long"],
        [[BAR, 100.0, 1, 0]],
    ), now=datetime.fromtimestamp(BAR / 1000, tz=timezone.utc))
    assert len(out) == 1
    assert out[0]["side"] == "enter_long"
    assert out[0]["price"] == 100.0
    # The candle's own time, not when we happened to read it: two sources agree
    # only if they agree about the same bar.
    assert out[0]["bar_time"].startswith("2025-08-19T")


def test_freqtrades_older_column_names_still_count():
    out = signals.extract(payload(
        ["__date_ts", "close", "buy", "sell"],
        [[BAR, 100.0, 0, 1]],
    ), now=datetime.fromtimestamp(BAR / 1000, tz=timezone.utc))
    assert [s["side"] for s in out] == ["exit_long"], "buy/sell must map to enter/exit"


def test_bars_with_no_signal_produce_nothing():
    out = signals.extract(payload(
        ["__date_ts", "close", "enter_long"],
        [[BAR, 100.0, 0], [BAR + 300000, 101.0, 0]],
    ), now=datetime.fromtimestamp(BAR / 1000, tz=timezone.utc))
    assert out == []


def test_old_bars_are_not_re_recorded_every_pass():
    """The dataframe is long; the window is short. Without this every check
    rewrites months of history to say nothing new."""
    out = signals.extract(payload(
        ["__date_ts", "close", "enter_long"],
        [[BAR, 100.0, 1]],
    ), lookback_hours=1, now=NOW)
    assert out == [], "a bar from a year ago is not news"


def test_an_iso_timestamp_column_works_when_the_epoch_one_is_absent():
    out = signals.extract(payload(
        ["date", "close", "enter_long"],
        [["2026-08-20T11:30:00+00:00", 100.0, 1]],
    ), now=NOW)
    assert len(out) == 1 and out[0]["bar_time"].startswith("2026-08-20T11:30")


def test_a_naive_timestamp_is_read_as_utc_rather_than_local():
    out = signals.extract(payload(
        ["date", "close", "enter_long"],
        [["2026-08-20 11:30:00", 100.0, 1]],
    ), now=NOW)
    assert len(out) == 1 and out[0]["bar_time"].endswith("+00:00")


@pytest.mark.parametrize("value,fires", [
    (1, True), (1.0, True), (True, True), ("1", True),
    (0, False), (0.0, False), (False, False), (None, False), ("", False),
])
def test_truthiness_matches_what_freqtrade_writes(value, fires):
    out = signals.extract(payload(
        ["__date_ts", "close", "enter_long"], [[BAR, 100.0, value]],
    ), now=datetime.fromtimestamp(BAR / 1000, tz=timezone.utc))
    assert bool(out) is fires, value


def test_a_payload_with_no_signal_columns_is_not_an_error():
    """Some strategies emit neither name; that is a strategy with no long side,
    not a fault."""
    assert signals.extract(payload(["__date_ts", "close"], [[BAR, 100.0]])) == []


def test_a_payload_with_no_timestamp_column_is_refused_rather_than_guessed():
    assert signals.extract(payload(["close", "enter_long"], [[100.0, 1]])) == []


def test_both_sides_of_one_bar_are_recorded_separately():
    out = signals.extract(payload(
        ["__date_ts", "close", "enter_long", "exit_long"],
        [[BAR, 100.0, 1, 1]],
    ), now=datetime.fromtimestamp(BAR / 1000, tz=timezone.utc))
    assert {s["side"] for s in out} == {"enter_long", "exit_long"}


def test_storing_is_idempotent_on_the_bar():
    """The bot re-reads a window that overlaps what it already stored."""
    class Bot:
        def get(self, path, params=None):
            return payload(["__date_ts", "close", "enter_long"], [[BAR, 100.0, 1]])

    class Client:
        def __init__(self):
            self.calls = []

        def upsert(self, table, rows, *, on_conflict, returning=True):
            self.calls.append((table, rows, on_conflict))
            return rows

    client = Client()
    signals.record(client, bot=Bot(), pairs=["BTC/USDT"], timeframe="5m",
                   owner_id="o", bot_instance_id="b", lookback_hours=10**6)
    table, rows, conflict = client.calls[0]
    assert table == "strategy_signals"
    assert conflict == "bot_instance_id,source,pair,timeframe,side,bar_time"
    assert rows[0]["owner_id"] == "o" and rows[0]["bot_instance_id"] == "b"


def test_one_unreadable_pair_does_not_cost_the_others():
    class Bot:
        def get(self, path, params=None):
            if params["pair"] == "BAD/USDT":
                raise RuntimeError("no dataframe")
            return payload(["__date_ts", "close", "enter_long"], [[BAR, 100.0, 1]])

    class Client:
        def __init__(self):
            self.rows = []

        def upsert(self, table, rows, *, on_conflict, returning=True):
            self.rows.extend(rows)
            return rows

    client = Client()
    stored = signals.record(client, bot=Bot(), pairs=["BAD/USDT", "BTC/USDT"],
                            timeframe="5m", owner_id=None, bot_instance_id=None,
                            lookback_hours=10**6)
    assert stored == 1 and len(client.rows) == 1


def test_a_write_failure_never_stops_the_bot():
    class Bot:
        def get(self, path, params=None):
            return payload(["__date_ts", "close", "enter_long"], [[BAR, 100.0, 1]])

    class Client:
        def upsert(self, *a, **k):
            raise RuntimeError("postgrest down")

    assert signals.record(Client(), bot=Bot(), pairs=["BTC/USDT"], timeframe="5m",
                          owner_id=None, bot_instance_id=None,
                          lookback_hours=10**6) == 0
