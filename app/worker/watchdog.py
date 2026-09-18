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
#: `stopped_with_positions` is deliberately not here either: being stopped is
#: the user's choice. It is recorded, and the dashboard shows it, so that
#: "stopped" and "stopped with unmanaged positions" never look the same.
NOTIFY_KINDS = {"offline", "not_trading"}

#: An incident that stays open this long is paged again. One page at minute
#: one was the whole alerting story of a five-day outage.
REPAGE_AFTER_SECONDS = 1800

#: The kinds this module opens, and therefore the only ones it resolves. Other
#: modules (the learning watch) open their own and close their own.
OWNED_KINDS = set(ALARMING.values()) | {"not_trading", "stopped_with_positions"}

#: Statuses a heartbeating bot can report that mean it is not trading. "hung"
#: is the bot's own verdict on itself: alive enough to answer, its trading
#: loop not going round.
NOT_TRADING_STATUSES = ("stopped", "paused", "unreachable", "hung")

#: How long after starting a bot is allowed to be up without serving yet.
#: A rolling deploy registers the replacement and starts its heartbeat before
#: its local API is answering, so for one sweep it reads alive-but-unreachable
#: and opened a `not_trading` incident -- seen at 06:28:45 on 2026-09-05,
#: resolved 61 seconds later, nothing wrong. `not_trading` pages, so that is a
#: page on every deploy, and the rule this module was written around is that a
#: watchdog which cries during deploys gets muted. A bot still not serving after
#: this long is not booting, and is reported.
STARTUP_GRACE_SECONDS = 300


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


def ping(url: str | None, timeout: int = 5) -> bool:
    """Touch a dead-man's switch. False when there is none or it did not answer."""
    if not url:
        return False
    request = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status < 300
    except Exception as exc:  # noqa: BLE001 - the switch is a witness, never a dependency
        log.warning("heartbeat ping failed: %s", exc)
        return False


def _seconds_since(stamp) -> int | None:
    try:
        then = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return int((_now() - then).total_seconds())
    except (ValueError, TypeError):
        return None


def _open_incident(client, bot: dict, kind: str, detail: str,
                   webhook_url: str | None) -> None:
    existing = client.select(
        "bot_incidents", columns="id,opened_at,last_paged_at,pages",
        filters={"bot_instance_id": f"eq.{bot['id']}", "kind": f"eq.{kind}",
                 "resolved_at": "is.null"},
        limit=1,
    )
    if existing:
        _repage(client, bot, kind, detail, existing[0], webhook_url)
        return

    log.warning("bot %s: %s -- %s", bot.get("name"), kind, detail)
    error = None
    if kind in NOTIFY_KINDS:
        error = notify(webhook_url, f"[{bot.get('name')}] {kind}: {detail}")
        if error:
            log.warning("could not send the alert: %s", error)

    paged = kind in NOTIFY_KINDS and error is None
    client.insert("bot_incidents", {
        "owner_id": bot.get("owner_id"),
        "bot_instance_id": bot["id"],
        "kind": kind,
        "detail": detail,
        "notified": paged,
        "notify_error": error,
        "last_paged_at": _now().isoformat() if paged else None,
        "pages": 1 if paged else 0,
    })


def _repage(client, bot: dict, kind: str, detail: str, incident: dict,
            webhook_url: str | None) -> None:
    """Page again about an incident that has stayed open too long.

    The first page is the one that gets missed: it arrives at minute one,
    when the outage looks like a blip. An incident still open half an hour
    later is not a blip, and says so again -- with how long it has been.
    """
    if kind not in NOTIFY_KINDS:
        return
    since_page = _seconds_since(incident.get("last_paged_at") or incident.get("opened_at"))
    if since_page is None or since_page < REPAGE_AFTER_SECONDS:
        return
    open_for = _seconds_since(incident.get("opened_at"))
    text = f"[{bot.get('name')}] still {kind}"
    if open_for is not None:
        text += f" after {open_for // 60} minutes"
    error = notify(webhook_url, f"{text}: {detail}")
    if error:
        log.warning("could not send the repeat alert: %s", error)
        return
    try:
        client.update("bot_incidents",
                      {"last_paged_at": _now().isoformat(),
                       "pages": int(incident.get("pages") or 0) + 1},
                      filters={"id": f"eq.{incident['id']}"})
    except Exception as exc:  # noqa: BLE001 - the page went out; that was the point
        log.warning("could not record the repeat page: %s", exc)


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
        if row.get("kind") in keep or row.get("kind") not in OWNED_KINDS:
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


def _still_booting(bot: dict) -> bool:
    """Has this bot started too recently to be judged for not serving yet?

    Only ever excuses a bot that reports `unreachable`. One deliberately
    stopped, or reporting `stopped`/`paused`, is not booting -- it is not
    trading, and that is worth saying however recently it started.
    """
    if str(bot.get("status") or "").lower() != "unreachable":
        return False
    try:
        uptime = float(bot.get("uptime_seconds"))
    except (TypeError, ValueError):
        # No started_at recorded: nothing says it is booting, so judge it.
        return False
    return 0 <= uptime < STARTUP_GRACE_SECONDS


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
              and str(bot.get("status") or "").lower() in NOT_TRADING_STATUSES
              and not _still_booting(bot)):
            reported = str(bot.get("status")).lower()
            open_trades = bot.get("open_trades") or 0
            detail = f"heartbeating normally but reporting {reported}."
            if reported == "hung":
                detail = ("heartbeating, answering, and its trading loop has not gone "
                          "round for minutes -- the process is up and the trader is dead.")
            if open_trades:
                detail += (f" {open_trades} position(s) open with no stop-loss "
                           "being applied.")
            wanted.add("not_trading")
            _open_incident(client, bot, "not_trading", detail, webhook_url)
            troubled += 1

        # Stopped on purpose, and holding positions. Not an outage -- nobody is
        # paged -- but a state worth a record and a banner, because "stopped"
        # and "stopped with nothing managing four open positions" must never
        # look the same on a dashboard.
        elif (intended != "running"
              and str(bot.get("status") or "").lower() in ("stopped", "paused")
              and (bot.get("open_trades") or 0) > 0):
            open_trades = bot.get("open_trades") or 0
            detail = (f"stopped on purpose with {open_trades} position(s) open; no "
                      "stop-loss is being managed on them until the bot is started.")
            wanted.add("stopped_with_positions")
            _open_incident(client, bot, "stopped_with_positions", detail, webhook_url)

        _resolve_incidents(client, bot, wanted, webhook_url)

    return troubled
