"""The worker's eye on the Learning Module: retention, and the stall nobody
would otherwise notice.

The bot publishes its own account of the writer once a minute. That row
being fresh proves the bot is up, not that decisions are arriving: an
adapter whose hooks all fail quietly publishes a perfectly healthy-looking
row forever. So this looks at three things the bot cannot vouch for itself
-- records piling up on the bot, records the database refused, and a
strategy that keeps producing signals while no decision has been recorded
for a day -- and opens a `learning_stalled` incident, which the dashboard
shows and nobody is paged for: it is a data problem, not a trading one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from app.worker import watchdog

log = logging.getLogger("worker.learning")

INCIDENT_KIND = "learning_stalled"

#: Days of decisions and events kept. A year is enough to learn from and
#: small enough to index; the prune runs once a day.
KEEP_DAYS = 365

#: More than this many records waiting on the bot means nothing is reaching
#: the database. Ten seconds of normal traffic is one or two.
PENDING_STALL = 50

#: No decision recorded for this long, while signals were, means the
#: adapter is not capturing. Longer than a day so a quiet weekend is not an
#: incident.
STALE_AFTER = timedelta(hours=26)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def prune(client, keep_days: int = KEEP_DAYS) -> int:
    """Remove decisions and events past retention. The one deletion path."""
    try:
        removed = client.rpc("prune_learning_records", {"p_keep_days": keep_days})
        if removed:
            log.info("pruned %s learning record(s) older than %s days", removed, keep_days)
        return int(removed or 0)
    except Exception as exc:  # noqa: BLE001 - housekeeping is never fatal
        log.warning("could not prune learning records: %s", exc)
        return 0


def _stall(client, row: dict) -> str | None:
    """Why this bot's pipeline looks stalled, or None."""
    pending = int(row.get("outbox_pending") or 0)
    if pending > PENDING_STALL:
        age = row.get("outbox_oldest_age_seconds")
        return (f"{pending} records are waiting on the bot"
                + (f", the oldest for {int(age)}s" if age else "")
                + "; nothing is reaching the database.")
    quarantined = int(row.get("outbox_quarantined") or 0)
    if quarantined:
        return (f"{quarantined} record(s) quarantined: the database refused them. "
                f"Last error: {str(row.get('last_error') or 'not given')[:160]}")
    orphaned = int(row.get("events_orphaned_24h") or 0)
    if orphaned:
        return (f"{orphaned} event(s) in the last day reference a decision that was never stored; "
                "the adapter is attaching events to the wrong decision id.")

    last_decision = _when(row.get("last_decision_at"))
    cutoff = _now() - STALE_AFTER
    if last_decision is not None and last_decision > cutoff:
        return None
    try:
        recent = client.select(
            "strategy_signals", columns="bar_time",
            filters={"bot_instance_id": f"eq.{row.get('bot_instance_id')}", "source": "eq.bot",
                     "side": "eq.enter_long", "bar_time": f"gte.{cutoff.isoformat()}"},
            limit=1,
        )
    except Exception as exc:  # noqa: BLE001 - without signals there is no case to make
        log.info("could not read strategy signals: %s", exc)
        return None
    if not recent:
        return None
    since = last_decision.isoformat() if last_decision else "the bot started"
    return (f"the strategy signalled at {recent[0].get('bar_time')} but no decision has been "
            f"recorded since {since}; the adapter may not be capturing.")


def _resolve(client, bot_id: str) -> None:
    try:
        open_rows = client.select(
            "bot_incidents", columns="id",
            filters={"bot_instance_id": f"eq.{bot_id}", "kind": f"eq.{INCIDENT_KIND}", "resolved_at": "is.null"},
        )
        for row in open_rows:
            client.update("bot_incidents", {"resolved_at": _now().isoformat()}, filters={"id": f"eq.{row['id']}"})
            log.info("learning pipeline recovered for bot %s", bot_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("could not resolve learning incidents: %s", exc)


def sweep(client) -> int:
    """One pass over every bot that publishes learning status. Returns how many are stalled."""
    try:
        rows = client.select("v_learning_health", columns="*")
    except Exception as exc:  # noqa: BLE001 - the view exists only once 0028 is applied
        log.info("no learning health to read: %s", exc)
        return 0
    names: dict[str, str] = {}
    if rows:
        try:
            ids = ",".join(str(r.get("bot_instance_id")) for r in rows if r.get("bot_instance_id"))
            for bot in client.select("bot_instances", columns="id,name", filters={"id": f"in.({ids})"}):
                names[str(bot["id"])] = bot.get("name") or str(bot["id"])
        except Exception as exc:  # noqa: BLE001
            log.info("could not name the bots: %s", exc)

    stalled = 0
    for row in rows:
        bot_id = str(row.get("bot_instance_id") or "")
        if not bot_id or not row.get("enabled", True):
            continue
        if (_now() - (_when(row.get("reported_at")) or _now())) > timedelta(minutes=10):
            continue  # the bot is not publishing; the watchdog's own incidents cover a dead bot
        detail = _stall(client, row)
        bot = {"id": bot_id, "name": names.get(bot_id, bot_id), "owner_id": row.get("owner_id")}
        if detail:
            stalled += 1
            try:
                watchdog._open_incident(client, bot, INCIDENT_KIND, detail, None)
            except Exception as exc:  # noqa: BLE001
                log.warning("could not open a learning incident: %s", exc)
        else:
            _resolve(client, bot_id)
    return stalled
