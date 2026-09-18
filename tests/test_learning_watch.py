"""The worker's eye on the Learning Module: a stalled pipeline becomes a
dashboard incident and nothing else; a healthy one closes it; retention runs
through the one function allowed to delete.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.worker import learning_watch, watchdog

NOW = datetime.now(timezone.utc)


def iso(**delta) -> str:
    return (NOW - timedelta(**delta)).isoformat()


class DB:
    def __init__(self, health=None, signals=None, incidents=None, rpc_result=0):
        self.data = {"v_learning_health": health or [], "strategy_signals": signals or [],
                     "bot_incidents": incidents or [], "bot_instances": [{"id": "b1", "name": "bot-staging"}]}
        self.inserted, self.updated, self.rpcs = [], [], []
        self.rpc_result = rpc_result

    def select(self, table, *, columns="*", filters=None, order=None, limit=None, offset=None):
        rows = list(self.data.get(table, []))
        for key, spec in (filters or {}).items():
            if spec.startswith("eq."):
                rows = [r for r in rows if str(r.get(key)) == spec[3:]]
            elif spec == "is.null":
                rows = [r for r in rows if r.get(key) is None]
        return rows

    def insert(self, table, row, **kwargs):
        self.inserted.append((table, row))
        return [row]

    def update(self, table, values, *, filters):
        self.updated.append((table, values, filters))
        return [values]

    def rpc(self, name, args=None):
        self.rpcs.append((name, args))
        return self.rpc_result


def health(**kw):
    base = {"bot_instance_id": "b1", "owner_id": "o1", "enabled": True, "reported_at": iso(minutes=1),
            "outbox_pending": 0, "outbox_quarantined": 0, "last_decision_at": iso(hours=1)}
    return {**base, **kw}


def test_records_piling_up_on_the_bot_open_an_incident_that_pages_nobody():
    db = DB(health=[health(outbox_pending=120, outbox_oldest_age_seconds=900)])
    assert learning_watch.sweep(db) == 1
    [(table, row)] = db.inserted
    assert table == "bot_incidents" and row["kind"] == "learning_stalled"
    assert "120 records are waiting" in row["detail"]
    assert row["notified"] is False and row["bot_instance_id"] == "b1"


def test_quarantined_records_are_an_incident_with_the_database_error():
    db = DB(health=[health(outbox_quarantined=3, last_error="trading_decisions: 400 invalid enum")])
    learning_watch.sweep(db)
    assert "quarantined" in db.inserted[0][1]["detail"]
    assert "invalid enum" in db.inserted[0][1]["detail"]


def test_a_strategy_that_signals_while_no_decision_arrives_is_a_stall():
    db = DB(health=[health(last_decision_at=iso(days=2))],
            signals=[{"bar_time": iso(hours=4), "bot_instance_id": "b1", "source": "bot", "side": "enter_long"}])
    assert learning_watch.sweep(db) == 1
    assert "no decision has been recorded" in db.inserted[0][1]["detail"]


def test_a_quiet_market_is_not_a_stall():
    db = DB(health=[health(last_decision_at=iso(days=2))], signals=[])
    assert learning_watch.sweep(db) == 0
    assert db.inserted == []


def test_a_healthy_pipeline_closes_its_open_incident():
    db = DB(health=[health()], incidents=[{"id": 9, "kind": "learning_stalled", "resolved_at": None,
                                          "bot_instance_id": "b1"}])
    assert learning_watch.sweep(db) == 0
    [(table, values, filters)] = db.updated
    assert table == "bot_incidents" and values["resolved_at"] and filters == {"id": "eq.9"}


def test_a_bot_that_stopped_publishing_is_left_to_the_watchdog():
    db = DB(health=[health(reported_at=iso(hours=3), outbox_pending=999)])
    assert learning_watch.sweep(db) == 0
    assert db.inserted == []


def test_an_existing_incident_is_not_opened_twice():
    db = DB(health=[health(outbox_pending=120)],
            incidents=[{"id": 9, "kind": "learning_stalled", "resolved_at": None, "bot_instance_id": "b1",
                        "opened_at": iso(minutes=30), "last_paged_at": None, "pages": 0}])
    learning_watch.sweep(db)
    assert db.inserted == []


def test_pruning_goes_through_the_one_function_allowed_to_delete():
    db = DB(rpc_result=17)
    assert learning_watch.prune(db) == 17
    assert db.rpcs == [("prune_learning_records", {"p_keep_days": learning_watch.KEEP_DAYS})]


def test_a_failing_read_never_raises():
    class Broken(DB):
        def select(self, *a, **k):
            raise RuntimeError("relation v_learning_health does not exist")

    assert learning_watch.sweep(Broken()) == 0
    assert learning_watch.prune(Broken()) == 0


def test_the_watchdog_leaves_the_learning_incident_alone():
    db = DB(incidents=[{"id": 9, "kind": "learning_stalled", "resolved_at": None, "bot_instance_id": "b1",
                        "opened_at": iso(minutes=30)}])
    watchdog._resolve_incidents(db, {"id": "b1", "name": "bot"}, keep=set(), webhook_url=None)
    assert db.updated == []


def test_events_pointing_at_a_missing_decision_are_a_stall():
    db = DB(health=[health(events_orphaned_24h=2)])
    assert learning_watch.sweep(db) == 1
    assert "never stored" in db.inserted[0][1]["detail"]
