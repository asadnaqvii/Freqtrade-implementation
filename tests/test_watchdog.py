"""Noticing that the trading bot has stopped.

It died on 31 August and again on 4 September, recovered by itself both times,
and the first anyone knew was days later going looking. v_bot_health could
always answer "is this alive"; nothing read it.

These tests are mostly about the ways a watchdog becomes useless: alerting on
things that are fine, alerting repeatedly for one outage, or falling over and
taking the worker with it. A muted watchdog is worse than none, because it
looks like coverage.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.worker import watchdog


class Client:
    def __init__(self, bots=None, open_incidents=None, boom=None):
        self.bots = bots or []
        self.open_incidents = open_incidents or []
        self.boom = boom
        self.inserted = []
        self.updated = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        if self.boom:
            raise self.boom
        if table == "v_bot_health":
            return list(self.bots)
        if table == "bot_incidents":
            rows = list(self.open_incidents)
            kind = (filters or {}).get("kind")
            if kind:
                rows = [r for r in rows if f"eq.{r['kind']}" == kind]
            return rows
        return []

    def insert(self, table, row):
        self.inserted.append((table, row))
        return [row]

    def update(self, table, values, *, filters=None):
        self.updated.append((table, values, dict(filters or {})))
        return [values]


def bot(**kw):
    base = {"id": "b1", "owner_id": "o1", "name": "freqtrade-bot",
            "status": "running", "desired_state": "running", "health": "healthy",
            "heartbeat_age_seconds": 12, "open_trades": 0,
            "uptime_seconds": 86400}
    return {**base, **kw}


@pytest.fixture(autouse=True)
def no_real_webhooks(monkeypatch):
    sent = []
    monkeypatch.setattr(watchdog, "notify",
                        lambda url, text, timeout=10: sent.append((url, text)))
    return sent


# ---------------------------------------------------------------------------
# What should raise an alarm
# ---------------------------------------------------------------------------

def test_a_dead_bot_opens_an_incident(no_real_webhooks):
    c = Client(bots=[bot(health="offline", heartbeat_age_seconds=3600)])
    assert watchdog.sweep(c) == 1
    table, row = c.inserted[0]
    assert table == "bot_incidents" and row["kind"] == "offline"
    assert row["bot_instance_id"] == "b1" and row["owner_id"] == "o1"


def test_open_positions_are_named_in_the_alert(no_real_webhooks):
    """The detail that turns an outage into a loss: nothing is managing the
    stop on those positions while it is down."""
    c = Client(bots=[bot(health="offline", open_trades=3)])
    watchdog.sweep(c)
    detail = c.inserted[0][1]["detail"]
    assert "3 position(s) open" in detail and "stop-loss" in detail


def test_heartbeating_but_stopped_is_an_incident(no_real_webhooks):
    """The most dangerous state: every dashboard looks green, the service is
    up, and no stop is being managed on anything it holds."""
    c = Client(bots=[bot(status="stopped", open_trades=2)])
    assert watchdog.sweep(c) == 1
    assert c.inserted[0][1]["kind"] == "not_trading"


def test_an_unreachable_api_counts_as_not_trading(no_real_webhooks):
    c = Client(bots=[bot(status="unreachable")])
    watchdog.sweep(c)
    assert c.inserted[0][1]["kind"] == "not_trading"


# ---------------------------------------------------------------------------
# What must not raise an alarm
# ---------------------------------------------------------------------------

def test_a_healthy_bot_is_silent(no_real_webhooks):
    c = Client(bots=[bot()])
    assert watchdog.sweep(c) == 0
    assert c.inserted == []


def test_a_bot_you_stopped_on_purpose_is_not_an_outage(no_real_webhooks):
    """Otherwise pressing Stop pages you, and a channel that fires when nothing
    is wrong is a channel you mute."""
    c = Client(bots=[bot(status="stopped", desired_state="stopped")])
    assert watchdog.sweep(c) == 0
    assert c.inserted == []


def test_a_bot_deliberately_stopped_and_now_offline_is_still_not_an_outage(no_real_webhooks):
    c = Client(bots=[bot(health="offline", desired_state="stopped")])
    assert watchdog.sweep(c) == 0


def test_a_bot_that_never_ran_is_not_an_outage(no_real_webhooks):
    """A row for a bot nobody deployed would otherwise page forever."""
    c = Client(bots=[bot(health="never_seen", last_heartbeat_at=None)])
    assert watchdog.sweep(c) == 0


def test_a_retired_bot_is_not_an_outage(no_real_webhooks):
    """The Railway instance was switched off on purpose at cutover. Its row is
    kept so its trade history still resolves, and it must never page again --
    an alert about a machine you decided to turn off is how a channel gets
    muted."""
    c = Client(bots=[bot(health="retired", desired_state=None)])
    assert watchdog.sweep(c) == 0
    assert c.inserted == []


def test_a_bot_still_booting_is_not_reported_as_not_trading(no_real_webhooks):
    """A rolling deploy registers the replacement and starts its heartbeat
    before its local API answers, so for one sweep it reads alive-but-
    unreachable. Seen at 06:28:45 on 2026-09-05, resolved 61 seconds later,
    nothing wrong -- and `not_trading` pages, so that is a page per deploy."""
    c = Client(bots=[bot(status="unreachable", uptime_seconds=20)])
    assert watchdog.sweep(c) == 0
    assert c.inserted == []


def test_a_bot_unreachable_long_after_starting_is_reported(no_real_webhooks):
    """The grace must expire. A bot that has been up an hour and still is not
    serving is not booting."""
    c = Client(bots=[bot(status="unreachable", uptime_seconds=3600)])
    assert watchdog.sweep(c) == 1
    assert c.inserted[0][1]["kind"] == "not_trading"


def test_a_freshly_started_but_stopped_bot_is_still_reported(no_real_webhooks):
    """The grace covers "not serving yet", not "serving and refusing to trade".
    A bot reporting stopped holds positions with no stop-loss on them, and that
    is true however recently it started."""
    c = Client(bots=[bot(status="stopped", uptime_seconds=20)])
    assert watchdog.sweep(c) == 1
    assert c.inserted[0][1]["kind"] == "not_trading"


def test_an_unknown_uptime_does_not_silence_the_check(no_real_webhooks):
    """Missing started_at must fail towards reporting. Silence should never be
    the default when the evidence is absent."""
    c = Client(bots=[bot(status="unreachable", uptime_seconds=None)])
    assert watchdog.sweep(c) == 1


def test_open_positions_are_counted_for_the_alert(no_real_webhooks):
    """trade_archive held closed trades only, so this count was always zero and
    the sentence that makes an outage urgent could never appear."""
    c = Client(bots=[bot(health="offline", open_trades=6)])
    watchdog.sweep(c)
    assert "6 position(s)" in c.inserted[0][1]["detail"]


def test_one_outage_opens_one_incident(no_real_webhooks):
    """Every sweep while it is down must not add a row; a ten-minute outage is
    one failure, not forty."""
    c = Client(bots=[bot(health="offline")],
               open_incidents=[{"id": 1, "kind": "offline", "opened_at": None}])
    watchdog.sweep(c)
    assert c.inserted == [], "it re-reported an outage it had already opened"


def test_a_deploy_does_not_page(no_real_webhooks):
    """`stale` is usually a redeploy and resolves itself in under a minute. It
    is recorded, but it does not push."""
    c = Client(bots=[bot(health="stale", heartbeat_age_seconds=400)])
    watchdog.sweep(c)
    assert c.inserted[0][1]["kind"] == "stale"
    assert no_real_webhooks == [], "a watchdog that cries on every deploy gets muted"


def test_going_offline_does_page(no_real_webhooks):
    c = Client(bots=[bot(health="offline")])
    watchdog.sweep(c, webhook_url="https://hooks.example/x")
    assert no_real_webhooks, "a dead bot must actually reach someone"
    assert "offline" in no_real_webhooks[0][1]


# ---------------------------------------------------------------------------
# Recovery
# ---------------------------------------------------------------------------

def test_recovery_closes_the_incident_and_records_the_downtime(no_real_webhooks):
    opened = (datetime.now(timezone.utc) - timedelta(minutes=7)).isoformat()
    c = Client(bots=[bot()],
               open_incidents=[{"id": 5, "kind": "offline", "opened_at": opened}])
    watchdog.sweep(c)
    table, values, filters = c.updated[0]
    assert table == "bot_incidents" and filters == {"id": "eq.5"}
    assert values["resolved_at"]
    assert 400 <= values["downtime_seconds"] <= 440, values["downtime_seconds"]


def test_a_still_failing_bot_keeps_its_incident_open(no_real_webhooks):
    c = Client(bots=[bot(health="offline")],
               open_incidents=[{"id": 5, "kind": "offline", "opened_at": None}])
    watchdog.sweep(c)
    assert c.updated == [], "it closed an incident that is still happening"


def test_an_unparseable_open_time_still_resolves(no_real_webhooks):
    c = Client(bots=[bot()],
               open_incidents=[{"id": 5, "kind": "offline", "opened_at": "not a date"}])
    watchdog.sweep(c)
    assert c.updated[0][1]["downtime_seconds"] is None
    assert c.updated[0][1]["resolved_at"], "a bad timestamp must not strand an incident"


# ---------------------------------------------------------------------------
# Never take the worker down
# ---------------------------------------------------------------------------

def test_a_database_failure_does_not_raise(no_real_webhooks):
    assert watchdog.sweep(Client(boom=RuntimeError("postgrest down"))) == 0


def test_a_failed_alert_is_recorded_rather_than_swallowed(monkeypatch):
    monkeypatch.setattr(watchdog, "notify",
                        lambda url, text, timeout=10: "connection refused")
    c = Client(bots=[bot(health="offline")])
    watchdog.sweep(c, webhook_url="https://hooks.example/x")
    row = c.inserted[0][1]
    assert row["notified"] is False and row["notify_error"] == "connection refused", (
        "an alert that failed to send must not be recorded as sent"
    )


def test_no_webhook_configured_still_records_the_incident(monkeypatch):
    monkeypatch.setattr(watchdog, "notify",
                        lambda url, text, timeout=10: "no webhook configured")
    c = Client(bots=[bot(health="offline")])
    watchdog.sweep(c)
    assert c.inserted, "the incident record is the fallback when nothing can be pushed"
