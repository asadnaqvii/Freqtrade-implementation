"""The recorder: one decision per candle, events that belong to it, nothing
invented and nothing lost -- and never an exception into the caller.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.learning.outbox import SqliteOutbox
from app.learning.recorder import Recorder

CANDLE = datetime(2026, 9, 17, 4, 0, tzinfo=timezone.utc)
NOW = CANDLE + timedelta(hours=4, seconds=5)
ROW = {"date": CANDLE, "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1, "volume": 10.0,
       "rsi": 61.2, "enter_long": 1, "exit_long": 0, "enter_tag": "pullback"}


@pytest.fixture
def box(tmp_path):
    outbox = SqliteOutbox(str(tmp_path / "outbox.sqlite"))
    yield outbox
    outbox.close()


@pytest.fixture
def recorder(box):
    identity = {"bot_name": "freqtrade-bot-staging", "bot_instance_id": None,
                "owner_id": "owner-1", "account_id": None}
    return Recorder(box, identity, environment="staging", exchange="kucoin",
                    strategy_id="TrendPullbackStrategy_v3",
                    provenance={"strategy_code_hash": "abc", "feature_set_version": "tp-v3.1"},
                    clock=lambda: NOW, log=lambda *_: None)


def queued(box, kind):
    return [json.loads(row.payload) for row in box.claim() if row.kind == kind]


def open_entry(recorder, **overrides):
    fields = dict(kind="entry", intent="enter_long", symbol="TRX/USDT", timeframe="4h",
                  candle=CANDLE, row=ROW, entry_tag="pullback")
    fields.update(overrides)
    return recorder.open_decision(**fields)


def test_one_decision_however_many_passes_see_the_signal(recorder, box):
    first = open_entry(recorder)
    again = open_entry(recorder)
    third = open_entry(recorder)
    assert first.new is True
    assert again.new is False and third.new is False
    assert again.decision_id == first.decision_id
    assert len(queued(box, "decision")) == 1
    assert recorder.health()["decisions_opened"] == 1
    assert recorder.health()["decisions_deduped"] == 2


def test_the_next_candle_is_a_new_decision(recorder, box):
    first = open_entry(recorder)
    later = open_entry(recorder, candle=CANDLE + timedelta(hours=4),
                       row={**ROW, "date": CANDLE + timedelta(hours=4)})
    assert later.new is True
    assert later.decision_id != first.decision_id
    assert later.decision_id > first.decision_id  # uuid7: later is later


def test_the_record_carries_what_the_bot_saw(recorder, box):
    open_entry(recorder, portfolio=lambda: {"available_stake": 40.0},
               risk=lambda: {"free_slots": 2})
    [row] = queued(box, "decision")
    assert row["symbol"] == "TRX/USDT"
    assert row["decision_kind"] == "entry" and row["strategy_intent"] == "enter_long"
    assert row["market_context"]["candle_open"] == "2026-09-17T04:00:00+00:00"
    assert row["market_data_max_ts"] == "2026-09-17T08:00:00+00:00"  # the candle's close
    assert row["feature_snapshot"] == {"rsi": 61.2}
    assert row["portfolio_snapshot"] == {"available_stake": 40.0}
    assert row["risk_snapshot"] == {"free_slots": 2}
    assert row["provenance"]["signals"] == {"enter_long": 1, "exit_long": 0, "enter_tag": "pullback"}
    assert row["strategy_code_hash"] == "abc"
    assert row["owner_id"] == "owner-1"
    assert row["entry_tag"] == "pullback"
    assert row["quarantined"] is False
    assert row["position_id"]  # minted for an entry


def test_a_candle_from_the_future_is_written_quarantined_not_dropped(recorder, box):
    future = NOW + timedelta(hours=1)
    opened = open_entry(recorder, candle=future, row={**ROW, "date": future})
    assert opened is not None and opened.new
    [row] = queued(box, "decision")
    assert row["quarantined"] is True
    assert row["quarantine_reason"].startswith("future_market_data")
    assert recorder.health()["decisions_quarantined"] == 1


def test_the_key_does_not_move_when_registration_finishes(recorder):
    before = open_entry(recorder)
    recorder.identity["bot_instance_id"] = "bot-uuid"
    after = open_entry(recorder)
    assert after.decision_id == before.decision_id


def test_a_changed_strategy_file_makes_the_same_candle_a_new_decision(recorder):
    before = open_entry(recorder)
    recorder.set_provenance(strategy_code_hash="def")
    after = open_entry(recorder)
    assert after.new and after.decision_id != before.decision_id


def test_an_exit_reuses_the_position_of_the_trade_it_closes(recorder):
    entry = open_entry(recorder)
    recorder.link_trade(7, "TRX/USDT", position_id=entry.position_id, origin_decision_id=entry.decision_id)
    exit_ = recorder.open_decision(kind="exit", intent="exit_long", symbol="TRX/USDT", timeframe="4h",
                                   candle=CANDLE, row=ROW, seq=7, ft_trade_id=7, exit_reason="roi")
    assert exit_.position_id == entry.position_id
    unknown = recorder.open_decision(kind="exit", intent="exit_long", symbol="XRP/USDT", timeframe="4h",
                                     candle=CANDLE, row=None, seq=9, ft_trade_id=9, exit_reason="roi")
    assert unknown.position_id == "ft:freqtrade-bot-staging:9"


def test_events_dedupe_on_their_key_and_are_counted(recorder, box):
    opened = open_entry(recorder)
    assert recorder.record_event(opened.decision_id, "signal_generated", key_time=CANDLE) is True
    assert recorder.record_event(opened.decision_id, "signal_generated", key_time=CANDLE) is False
    [event] = queued(box, "event")
    assert event["decision_id"] == opened.decision_id
    assert event["symbol"] == "TRX/USDT"
    assert event["position_id"] == opened.position_id
    assert event["event_time_utc"] == NOW.isoformat()
    assert recorder.health()["events_recorded"] == 1
    assert recorder.health()["events_deduped"] == 1


def test_a_repeated_rejection_is_a_metric_not_a_second_event(recorder, box):
    opened = open_entry(recorder)
    assert recorder.reject(opened.decision_id, "PAIR_LOCKED", "bot", payload={"lock": "x"}) is True
    assert recorder.reject(opened.decision_id, "PAIR_LOCKED", "bot") is False
    assert recorder.reject(opened.decision_id, "PAIR_LOCKED", "bot") is False
    assert recorder.repeat_count(opened.decision_id, "PAIR_LOCKED") == 3
    assert recorder.health()["rejections"] == {"PAIR_LOCKED": 3}
    events = queued(box, "event")
    assert [e["rejection_code"] for e in events] == ["PAIR_LOCKED"]
    assert events[0]["rejection_stage"] == "bot"
    assert recorder.is_terminal(opened.decision_id)
    # a different reason is a different event
    assert recorder.reject(opened.decision_id, "MAX_OPEN_TRADES", "bot") is True


def test_contexts_follow_one_pair_at_a_time(recorder):
    opened = open_entry(recorder)
    ctx = recorder.begin("TRX/USDT", opened.decision_id, "entry")
    assert recorder.context("TRX/USDT") is ctx
    assert ctx.position_id == opened.position_id
    assert recorder.context("XRP/USDT") is None
    recorder.end("TRX/USDT")
    assert recorder.context("TRX/USDT") is None


def test_an_order_is_resolved_by_its_id_first_and_its_trade_second(recorder):
    entry = open_entry(recorder)
    recorder.link_trade(7, "TRX/USDT", position_id=entry.position_id, origin_decision_id=entry.decision_id)
    recorder.link_order("order-1", decision_id=entry.decision_id, position_id=entry.position_id,
                        ft_trade_id=7, side="buy")
    assert recorder.decision_for_order("order-1", 7, True) == (entry.decision_id, entry.position_id)
    assert recorder.decision_for_order("unknown", 7, True) == (entry.decision_id, entry.position_id)
    recorder.exit_decisions[7] = "exit-decision"
    assert recorder.decision_for_order(None, 7, False) == ("exit-decision", entry.position_id)
    assert recorder.decision_for_order(None, 99, True) == (None, None)
    assert recorder.trade_for_pair("TRX/USDT") == 7
    recorder.close_trade(7)
    assert recorder.trade_for_pair("TRX/USDT") is None


def test_a_snapshot_that_fails_is_marked_and_counted_not_raised(recorder, box):
    def broken():
        raise RuntimeError("wallets away")

    opened = open_entry(recorder, portfolio=broken)
    assert opened is not None
    [row] = queued(box, "decision")
    assert row["portfolio_snapshot"] == {"_unavailable": "wallets away"}
    assert recorder.health()["adapter_errors"] == 1
    assert "portfolio_snapshot" in recorder.health()["last_error"]


def test_a_broken_outbox_never_reaches_the_caller(recorder, monkeypatch):
    def explode(*args, **kwargs):
        raise RuntimeError("disk gone")

    monkeypatch.setattr(recorder.outbox, "enqueue", explode)
    assert open_entry(recorder) is None
    assert recorder.record_event("d", "signal_generated") is False
    assert recorder.health()["adapter_errors"] == 2


def test_the_correlation_is_also_kept_in_the_outbox_file(recorder, box):
    entry = open_entry(recorder)
    recorder.link_trade(7, "TRX/USDT", position_id=entry.position_id, origin_decision_id=entry.decision_id)
    assert box.correlation(7) == (entry.position_id, entry.decision_id)


def test_a_restart_within_the_same_candle_keeps_the_decisions_identity(box):
    """The container is replaced on every deploy, and a new process knows
    nothing. The same candle and the same key must still be the same decision,
    or the new process's events dangle from an id the database never stored."""
    identity = {"bot_name": "freqtrade-bot-staging", "bot_instance_id": None, "owner_id": "owner-1"}
    provenance = {"strategy_code_hash": "abc", "feature_set_version": "tp-v3.1"}

    def fresh_process():
        return Recorder(box, dict(identity), environment="staging", exchange="kucoin",
                        strategy_id="TrendPullbackStrategy_v3", provenance=provenance,
                        clock=lambda: NOW, log=lambda *_: None)

    first = open_entry(fresh_process())
    again = open_entry(fresh_process())
    assert again.new is True, "a fresh process does not remember; the ids must not depend on memory"
    assert again.decision_id == first.decision_id
    assert again.position_id == first.position_id
    assert len(queued(box, "decision")) == 1
    later = open_entry(fresh_process(), candle=CANDLE + timedelta(hours=4),
                       row={**ROW, "date": CANDLE + timedelta(hours=4)})
    assert later.decision_id != first.decision_id and later.decision_id > first.decision_id
