"""The Learning API: a decision and its timeline come back in one piece, the
filters narrow, the page is calm before the first decision, and every field,
stage and reason comes with a sentence.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException

from app.api.routers import learning as api
from app.learning.enums import EventType, RejectionCode

NOW = datetime.now(timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


class DB:
    def __init__(self, decisions=None, events=None, health=None, missing=()):
        self.data = {"trading_decisions": decisions or [], "trading_events": events or [],
                     "v_learning_health": health or []}
        self.missing = set(missing)
        self.calls = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None, offset=None):
        self.calls.append((table, filters))
        if table in self.missing:
            raise RuntimeError(f"relation {table} does not exist")
        rows = list(self.data.get(table, []))
        for key, spec in (filters or {}).items():
            if spec.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == spec[3:]]
        return rows


def decision(**kw):
    base = {"decision_id": "d1", "decision_time_utc": iso(30), "symbol": "TRX/USDT", "timeframe": "4h",
            "decision_kind": "entry", "strategy_intent": "enter_long", "strategy_id": "S",
            "entry_tag": "pullback", "quarantined": False, "position_id": "p1",
            "market_context": {"candle_open": (NOW - timedelta(hours=4)).replace(minute=0).isoformat()},
            "feature_snapshot": {"rsi": 61.2, "adx_falling": None}}
    return {**base, **kw}


def event(**kw):
    base = {"event_id": "e", "decision_id": "d1", "event_type": "signal_generated",
            "event_time_utc": iso(29), "event_source": "freqtrade_callback", "payload": {}}
    return {**base, **kw}


def run(coro):
    return asyncio.run(coro)


def test_a_decision_and_its_timeline_come_back_in_one_piece():
    db = DB(decisions=[decision()], events=[
        event(event_id="e1", event_type="signal_generated", event_time_utc=iso(29)),
        event(event_id="e2", event_type="order_submitted", event_time_utc=iso(28)),
        event(event_id="e3", event_type="fill_completed", event_time_utc=iso(27), exchange_order_id="x1"),
        event(event_id="e4", event_type="position_opened", event_time_utc=iso(26)),
    ])
    out = run(api.decision(db, "d1"))
    assert out["decision"]["symbol"] == "TRX/USDT"
    assert [t["stage"] for t in out["timeline"]] == ["signal", "order", "fill", "position"]
    assert all(t["meaning"] for t in out["timeline"])
    assert out["summary"]["outcome"] == "executed"
    assert "TRX/USDT" in out["summary"]["headline"] and "filled" in out["summary"]["headline"]
    assert out["explanations"]["decision"]["feature_snapshot"].startswith("Every indicator value")
    assert out["explanations"]["event"]["event_type"]


def test_a_rejected_decision_says_why_in_plain_words():
    db = DB(decisions=[decision()], events=[
        event(event_id="e1"),
        event(event_id="e2", event_type="signal_rejected", rejection_code="MAX_OPEN_TRADES",
              rejection_stage="bot", event_time_utc=iso(28)),
    ])
    out = run(api.decisions(db))
    [item] = out["items"]
    assert item["summary"]["outcome"] == "rejected"
    assert item["summary"]["reason"] == "MAX_OPEN_TRADES"
    assert item["summary"]["reason_meaning"] == "Every trade slot was already in use, so this signal could not be taken."
    assert "did not" in item["summary"]["headline"]
    assert out["counts"] == {"rejected": 1}


def test_filters_narrow_by_pair_outcome_and_feature():
    db = DB(decisions=[decision(), decision(decision_id="d2", symbol="XRP/USDT", feature_snapshot={"rsi": 40})],
            events=[event(decision_id="d1", event_type="fill_completed")])
    assert [i["decision"]["decision_id"] for i in run(api.decisions(db, pair="XRP/USDT"))["items"]] == ["d2"]
    assert [i["decision"]["decision_id"] for i in run(api.decisions(db, outcome="executed"))["items"]] == ["d1"]
    assert [i["decision"]["decision_id"] for i in run(api.decisions(db, feature=["rsi:gt:50"]))["items"]] == ["d1"]
    assert run(api.decisions(db, feature=["rsi:lt:50"]))["items"][0]["decision"]["decision_id"] == "d2"
    with pytest.raises(HTTPException):
        run(api.decisions(db, feature=["rsi>50"]))


def test_the_page_is_calm_before_the_first_decision():
    out = run(api.decisions(DB()))
    assert out["items"] == [] and out["total"] == 0
    assert "No decisions recorded" in out["note"]
    missing = run(api.decisions(DB(missing=("trading_decisions", "trading_events"))))
    assert missing["items"] == []
    assert "not readable yet" in missing["note"]
    with pytest.raises(HTTPException) as exc:
        run(api.decision(DB(), "nope"))
    assert exc.value.status_code == 404


def test_health_explains_every_number_and_says_when_nothing_has_been_published():
    empty = run(api.health(DB()))
    assert empty["bots"] == [] and "not published" in empty["note"] or "No bot" in empty["note"]
    row = {"bot_instance_id": "b", "outbox_pending": 0, "decisions_24h": 3, "reported_at": iso(1)}
    out = run(api.health(DB(health=[row])))
    assert out["bots"] == [row]
    for key in row:
        assert key in out["meaning"], key


def test_the_glossary_covers_every_event_type_and_rejection_code():
    out = run(api.glossary())
    for event_type in EventType:
        assert event_type.value in out["events"], event_type
    for code in RejectionCode:
        assert code.value in out["rejections"], code
    for key in ("decision_id", "feature_snapshot", "market_data_max_ts", "payload", "rejection_code"):
        assert key in out["fields"]


def test_a_quarantined_record_is_shown_and_labelled():
    db = DB(decisions=[decision(quarantined=True, quarantine_reason="future_market_data: ...")])
    [item] = run(api.decisions(db))["items"]
    assert item["summary"]["outcome"] == "quarantined"
    assert "kept aside" in item["summary"]["headline"]


def test_a_position_gathers_its_decisions_in_order():
    db = DB(decisions=[decision(decision_time_utc=iso(60)),
                       decision(decision_id="d2", decision_kind="exit", strategy_intent="exit_long",
                                exit_reason="roi", decision_time_utc=iso(10))],
            events=[event(decision_id="d1", position_id="p1", event_type="position_opened", event_time_utc=iso(59)),
                    event(event_id="e9", decision_id="d2", position_id="p1", event_type="position_closed",
                          event_time_utc=iso(9))])
    out = run(api.position(db, "p1"))
    assert [d["decision"]["decision_kind"] for d in out["decisions"]] == ["entry", "exit"]
    assert [t["event_type"] for t in out["timeline"]] == ["position_opened", "position_closed"]
