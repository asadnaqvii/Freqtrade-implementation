"""Verification, referenced from the decisions it checked: one reference per
run, one verdict per finding on the decision that placed the order, and an
order nobody decided on recorded as exactly that.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from app.api.routers import verification as chain_api
from app.learning import verification_link


class DB:
    def __init__(self, links=None):
        self.links = links or []
        self.written = []
        self.filters = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None, offset=None):
        self.filters.append(filters)
        return list(self.links)

    def insert_new_only(self, table, rows, *, on_conflict):
        self.written.append((table, rows, on_conflict))


def finding(**kw):
    base = {"pair": "TRX/USDT", "ft_order_id": "12", "exchange_order_id": "ex-1", "matched": True,
            "discrepancy_kind": None, "discrepancy_pct": None, "notes": "filled as recorded"}
    return {**base, **kw}


def test_verdicts_land_on_the_decision_that_placed_the_order():
    db = DB(links=[{"exchange_order_id": "ex-1", "decision_id": "d1", "position_id": "p1",
                    "symbol": "TRX/USDT", "ft_trade_id": 7}])
    sent = verification_link.record_reconciliation(
        db, run_id="run-1", bot_instance_id="b1", owner_id="o1",
        findings=[finding(), finding(exchange_order_id="ex-2", matched=False, discrepancy_kind="price_mismatch")],
    )
    assert sent == 3
    [(table, rows, conflict)] = db.written
    assert table == "trading_events" and conflict == "idempotency_key"
    assert rows[0]["event_type"] == "verification_reference" and rows[0]["decision_id"] is None
    assert rows[0]["payload"] == {"run_id": "run-1", "findings": 2, "matched": 1, "disputed": 1}
    linked = rows[1]
    assert linked["event_type"] == "verification_status_changed"
    assert linked["decision_id"] == "d1" and linked["position_id"] == "p1" and linked["ft_trade_id"] == 7
    assert linked["payload"]["verdict"] == "matched"
    unlinked = rows[2]
    assert unlinked["decision_id"] is None and unlinked["payload"]["verdict"] == "price_mismatch"
    assert db.filters[0]["exchange_order_id"] == 'in.("ex-1","ex-2")'


def test_an_order_nobody_decided_on_is_recorded_as_exactly_that():
    db = DB()
    verification_link.record_reconciliation(
        db, run_id="run-2", bot_instance_id="b1", owner_id="o1",
        findings=[finding(ft_order_id=None, exchange_order_id="stranger", matched=False,
                          discrepancy_kind="unknown_order")],
    )
    [(_, rows, _)] = db.written
    assert rows[1]["event_type"] == "unaccounted_exchange_activity"
    assert rows[1]["decision_id"] is None and rows[1]["exchange_order_id"] == "stranger"


def test_the_same_run_recorded_twice_has_the_same_keys():
    when = datetime(2026, 9, 17, 9, 0, tzinfo=timezone.utc)
    keys = []
    for _ in range(2):
        db = DB()
        verification_link.record_reconciliation(db, run_id="run-3", bot_instance_id="b1", owner_id="o1",
                                                findings=[finding()], checked_at=when)
        keys.append([r["idempotency_key"] for r in db.written[0][1]])
    assert keys[0] == keys[1]


def test_findings_may_arrive_as_objects_with_as_row():
    class Finding:
        def as_row(self, run_id, bot_instance_id, account_id):
            return finding(exchange_order_id="ex-9")

    db = DB()
    assert verification_link.record_reconciliation(db, run_id="r", bot_instance_id=None, owner_id=None,
                                                   findings=[Finding()]) == 2


def test_the_chain_names_the_decision_and_the_reason(monkeypatch):
    now = datetime.now(timezone.utc)
    bar = (now - timedelta(minutes=60)).isoformat()

    class ChainDB:
        def select(self, table, *, columns="*", filters=None, order=None, limit=None, offset=None):
            return {
                "strategy_signals": [{"source": "bot", "pair": "TRX/USDT", "timeframe": "5m", "side": "enter_long",
                                      "bar_time": bar, "price": 1.0},
                                     {"source": "bot", "pair": "XRP/USDT", "timeframe": "5m", "side": "enter_long",
                                      "bar_time": bar, "price": 1.0}],
                "v_live_orders": [{"ft_order_id": "1", "ft_trade_id": 1, "pair": "TRX/USDT", "exchange_order_id": "ex-1",
                                   "status": "closed", "side": "buy", "price": 1.0, "average": 1.0, "amount": 1.0,
                                   "filled": 1.0, "order_date": (now - timedelta(minutes=58)).isoformat()}],
                "order_reconciliations": [],
                "trading_events": [
                    {"decision_id": "d1", "event_type": "order_acknowledged", "exchange_order_id": "ex-1",
                     "symbol": "TRX/USDT", "event_time_utc": (now - timedelta(minutes=58)).isoformat()},
                    {"decision_id": "d2", "event_type": "signal_rejected", "exchange_order_id": None,
                     "symbol": "XRP/USDT", "event_time_utc": (now - timedelta(minutes=59)).isoformat(),
                     "rejection_code": "PAIR_LOCKED"},
                ],
            }[table]

    out = asyncio.run(chain_api.chain(ChainDB()))
    by_pair = {r["pair"]: r for r in out["rows"]}
    assert by_pair["TRX/USDT"]["decision_id"] == "d1"
    assert by_pair["XRP/USDT"]["outcome"] == "signal_not_acted"
    assert by_pair["XRP/USDT"]["rejection_code"] == "PAIR_LOCKED"
    assert "locked" in by_pair["XRP/USDT"]["rejection_meaning"]
