"""Notice when the trading bot stops, and say so.

The bot died on 31 August and again on 4 September. Both times it recovered by
itself, and both times the first anyone knew was days later, going looking.
For a process trading real money that is the wrong way to find out.

`v_bot_health` could always answer "is this bot alive". Nothing read it. This
does, from the worker -- a separate always-on process, so it is still there to
notice when the bot is the thing that died. A watchdog inside the bot would go
down with it, which is the one moment it is needed.

Three states are worth an incident, and they are not the same failure:

  offline      no heartbeat for a long time. The process is gone.
  stale        heartbeats have stopped but not long enough to be sure. Often a
               deploy, which is why it resolves on its own and is reported more
               quietly.
  not_trading  heartbeating, reachable, and STOPPED. Nothing is wrong with the
               machinery and no stop-loss is being managed either -- the most
               dangerous of the three, because every dashboard looks healthy.

Deliberately no paging on `stale` alone. A watchdog that cries during every
deploy gets muted, and a muted watchdog is worse than none.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("worker.watchdog")

#: Health values that open an incident, mapped to the kind recorded.
ALARMING = {"offline": "offline", "stale": "stale"}

#: Only these get pushed. `stale` is usually a deploy in progress and resolves
#: itself within a minute; paging on it teaches you to ignore the channel.
NOTIFY_KINDS = {"offline", "not_trading"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def notify(webhook_url: str | None, text: str, timeout: int = 10) -> str | None:
    """Push a line somewhere a person will see it. Returns an error, or None.

    Deliberately a plain webhook rather than one vendor's SDK: the same posted
    JSON works for Slack, Discord, ntfy and Telegram's sendMessage, so the
    channel is a configuration decision rather than a code change.
    """
    if not webhook_url:
        return "no webhook configured"
    payload = json.dumps({"text": text, "content": text, "message": text}).encode()
    request = urllib.request.Request(
        webhook_url, data=payload, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            if response.status >= 300:
                return f"webhook returned {response.status}"
        return None
    except urllib.error.HTTPError as exc:
        return f"webhook returned {exc.code}"
    except Exception as exc:  # noqa: BLE001 - a failed page must not stop the sweep
        return str(exc)


def _open_incident(client, bot: dict, kind: str, detail: str,
                   webhook_url: str | None) -> None:
    existing = client.select(
        "bot_incidents", columns="id",
        filters={"bot_instance_id": f"eq.{bot['id']}", "kind": f"eq.{kind}",
                 "resolved_at": "is.null"},
        limit=1,
    )
    if existing:
        return                                  # already open; do not re-page

    log.warning("bot %s: %s -- %s", bot.get("name"), kind, detail)
    error = None
    if kind in NOTIFY_KINDS:
        error = notify(webhook_url, f"[{bot.get('name')}] {kind}: {detail}")
        if error:
            log.warning("could not send the alert: %s", error)

    client.insert("bot_incidents", {
        "owner_id": bot.get("owner_id"),
        "bot_instance_id": bot["id"],
        "kind": kind,
        "detail": detail,
        "notified": kind in NOTIFY_KINDS and error is None,
        "notify_error": error,
    })


def _resolve_incidents(client, bot: dict, keep: set[str],
                       webhook_url: str | None) -> None:
    """Close anything open for this bot that is no longer true."""
    try:
        open_rows = client.select(
            "bot_incidents", columns="id,kind,opened_at",
            filters={"bot_instance_id": f"eq.{bot['id']}", "resolved_at": "is.null"},
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read open incidents: %s", exc)
        return

    for row in open_rows:
        if row.get("kind") in keep:
            continue
        opened = row.get("opened_at")
        seconds = None
        try:
            started = datetime.fromisoformat(str(opened).replace("Z", "+00:00"))
            seconds = int((_now() - started).total_seconds())
        except (ValueError, TypeError):
            pass
        client.update("bot_incidents",
                      {"resolved_at": _now().isoformat(), "downtime_seconds": seconds},
                      filters={"id": f"eq.{row['id']}"})
        log.info("bot %s recovered from %s after %ss", bot.get("name"),
                 row.get("kind"), seconds)
        if row.get("kind") in NOTIFY_KINDS:
            notify(webhook_url,
                   f"[{bot.get('name')}] recovered from {row.get('kind')}"
                   + (f" after {seconds}s" if seconds is not None else ""))


def sweep(client, *, webhook_url: str | None = None) -> int:
    """One pass over every bot. Returns how many are currently in trouble."""
    try:
        bots = client.select("v_bot_health", columns="*", order="name.asc")
    except Exception as exc:  # noqa: BLE001 - never take the worker down for this
        log.warning("could not read bot health: %s", exc)
        return 0

    troubled = 0
    for bot in bots:
        # A bot that has never checked in was never deployed; that is not an
        # outage, and paging about it on every sweep forever helps nobody. A
        # retired one was switched off on purpose -- the Railway instance at
        # cutover -- and its row is kept only so its trade history still
        # resolves. Neither is a machine anybody wants waking them at 4am.
        health = bot.get("health")
        if health in ("never_seen", "retired"):
            continue

        # A bot somebody deliberately stopped is not an outage. Paging on it
        # would fire every time the Stop button is used, and a channel that
        # alerts when nothing is wrong is a channel that gets muted.
        intended = str(bot.get("desired_state") or "running").lower()

        wanted: set[str] = set()
        if health in ALARMING and intended == "running":
            kind = ALARMING[health]
            age = bot.get("heartbeat_age_seconds")
            try:
                age_text = f"{int(float(age))}s since the last heartbeat"
            except (TypeError, ValueError):
                age_text = "no recent heartbeat"
            open_trades = bot.get("open_trades") or 0
            detail = f"{age_text}."
            if open_trades:
                # The part that turns an outage into a loss: nothing is
                # managing the stop on those positions while it is down.
                detail += (f" {open_trades} position(s) open and unmanaged -- "
                           "no stop-loss is being applied while it is down.")
            wanted.add(kind)
            _open_incident(client, bot, kind, detail, webhook_url)
            troubled += 1

        # Alive, answering, and not trading. Nothing looks wrong anywhere --
        # the heartbeat is fresh and the service is green -- and no stop is
        # being managed on anything it holds.
        elif (intended == "running"
              and str(bot.get("status") or "").lower() in ("stopped", "paused",
                                                           "unreachable")):
            reported = str(bot.get("status")).lower()
            open_trades = bot.get("open_trades") or 0
            detail = f"heartbeating normally but reporting {reported}."
            if open_trades:
                detail += (f" {open_trades} position(s) open with no stop-loss "
                           "being applied.")
            wanted.add("not_trading")
            _open_incident(client, bot, "not_trading", detail, webhook_url)
            troubled += 1

        _resolve_incidents(client, bot, wanted, webhook_url)

    return troubled
