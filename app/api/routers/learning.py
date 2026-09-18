"""The Learning Module's memory, readable: every decision the bot recorded,
what happened to it, and what each part of the record means.

Read-only by design, like verification (0017's reasoning): a dashboard that
could write decisions could hide a missed trade. Every read is scoped by RLS
through the caller's own token, and every one degrades to an empty answer
with a note rather than an error, because the tables are new and a
deployment that has not recorded anything yet is not broken.

Two things this returns that the tables do not hold: an *outcome* per
decision, derived from its events, and plain-language explanations for
every field, stage and reason -- the page shows the raw record and the
sentence beside it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import UserDB
from app.learning.enums import REJECTION_MEANING

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/learning", tags=["learning"])

#: The most decisions one page will read and explain.
PAGE_LIMIT = 200
#: How many decisions a window is scanned for before feature filters apply.
SCAN_LIMIT = 500

#: The stages of a decision's life, in order, and what each event means.
STAGES: list[tuple[str, str, str]] = [
    ("signal_generated", "signal", "The strategy's rules matched on this candle: it wanted to enter."),
    ("exit_signal_generated", "signal", "The strategy's rules said it was time to leave this position."),
    ("signal_rejected", "risk", "The bot did not act on the signal. The reason code says why."),
    ("risk_decision", "risk", "A protection rule fired and locked trading."),
    ("bot_instruction_created", "instruction",
     "The bot decided how much to trade and at what price, and prepared the order."),
    ("execution_plan", "instruction", "The bot worked out how the order would be placed."),
    ("order_submitted", "order", "The order was sent to the exchange."),
    ("order_acknowledged", "ack", "The exchange accepted the order and gave it an id."),
    ("order_rejected", "ack", "The exchange refused the order."),
    ("order_cancelled", "fill", "The order was cancelled before it filled."),
    ("partial_fill", "fill", "Part of the order filled before it ended."),
    ("fill_completed", "fill", "The order filled completely."),
    ("position_opened", "position", "The position is now open."),
    ("position_adjusted", "position", "The position's size changed: an added entry or a partial exit."),
    ("position_closed", "exit", "The position is closed; its profit or loss is final."),
    ("verification_reference", "verification",
     "A verification run checked this decision's orders against the exchange."),
    ("verification_status_changed", "verification",
     "The exchange's verdict on one of this decision's orders."),
    ("unaccounted_exchange_activity", "verification",
     "The exchange reports activity the bot did not decide on."),
    ("unlinked_position_observed", "position",
     "A position was found that this module did not see opened; nothing is invented for it."),
    ("bot_status", "bot", "The bot reported its own status."),
]
STAGE_OF = {event: stage for event, stage, _ in STAGES}
EVENT_MEANING = {event: meaning for event, _, meaning in STAGES}
STAGE_ORDER = ["signal", "risk", "instruction", "order", "ack", "fill", "position", "exit",
               "verification", "bot"]

KIND_MEANING = {
    "entry": "Open a new position.",
    "exit": "Close a position.",
    "add": "Add to a position that is already open.",
    "reduce": "Sell part of a position and keep the rest.",
}
INTENT_MEANING = {
    "enter_long": "Buy, expecting the price to rise.",
    "enter_short": "Sell short, expecting the price to fall.",
    "exit_long": "Sell a position that was bought.",
    "exit_short": "Buy back a position that was sold short.",
}
OUTCOME_MEANING = {
    "executed": "The bot acted and the order filled.",
    "placed": "The order is on the exchange and has not filled yet.",
    "rejected": "The signal did not become a trade. The reason says why.",
    "cancelled": "An order was placed and then cancelled before it filled.",
    "signalled": "The strategy signalled and nothing has happened yet.",
    "quarantined": "The record failed its own consistency check and is kept aside, never used for learning.",
    "observed": "Activity the bot did not decide on, recorded for completeness.",
}

#: What every key of a decision record means, in the words the page shows.
FIELDS = {
    "decision_id": "The record's unique id. Time-ordered: a later id is a later decision.",
    "decision_time_utc": "When the bot made this decision (UTC).",
    "recorded_at_utc": "When the record reached the database. Later than the decision by seconds, normally.",
    "environment": "Which deployment recorded it: production, staging, or a smoke test.",
    "exchange": "The venue the bot trades on.",
    "symbol": "The trading pair, base/quote.",
    "timeframe": "The candle size the strategy runs on.",
    "decision_kind": "What the bot meant to do: entry, exit, add or reduce.",
    "strategy_intent": "The direction: enter long, exit long, and so on.",
    "position_id": "Ties every decision about one position together, from entry to exit.",
    "strategy_id": "The strategy class that decided.",
    "strategy_version": "The strategy's interface version.",
    "strategy_code_hash": "A fingerprint of the strategy file. A different hash means different code.",
    "parameter_hash": "A fingerprint of the parameter values the strategy was running with.",
    "runtime_config_hash": "A fingerprint of the bot's configuration, with every secret removed first.",
    "feature_set_version": "Which set of indicator columns the snapshot carries. Snapshots with different versions are not comparable.",
    "entry_tag": "The label the strategy put on the entry signal.",
    "exit_reason": "Why the position was closed: exit signal, ROI, stop-loss, trailing stop, forced.",
    "market_data_max_ts": "The newest market data the decision could have seen: the close of the last finished candle.",
    "market_context": "The candle the strategy was looking at: open, high, low, close, volume and its times.",
    "feature_snapshot": "Every indicator value the strategy had on that candle. These are the numbers the rules were applied to.",
    "portfolio_snapshot": "What the bot held and could spend at that moment: balance, free stake, open positions.",
    "risk_snapshot": "What constrained the decision: locks, free trade slots, the stop-loss, the protections.",
    "prediction_snapshot": "Reserved for a model's prediction. Always empty until a model exists.",
    "provenance": "Which exact code, parameters and settings produced this decision, and how the record was captured.",
    "quarantined": "True when the record failed its own consistency check. Kept, shown, never used for learning.",
    "quarantine_reason": "Why the record was quarantined.",
    "idempotency_key": "The key that makes this decision one row however many times it was captured.",
    "payload_sha256": "A hash of the record's contents. Two captures of the same decision hash the same.",
    "schema_version": "The version of this record's layout.",
    "owner_id": "The account this record belongs to.",
    "bot_instance_id": "Which bot instance recorded it.",
    "account_id": "Which exchange account the bot was trading.",
    "run_id": "Set when a decision came from a backtest run rather than the live bot.",
    "model_id": "Reserved for a model's id. Empty until a model exists.",
    "model_version": "Reserved for a model's version.",
    "event_id": "The event's unique id, time-ordered.",
    "event_type": "What happened. Each type is one stage of a decision's life.",
    "event_time_utc": "When it happened (UTC).",
    "event_source": "Who reported it: freqtrade's own callbacks or messages, the learning adapter, or the verifier.",
    "ft_trade_id": "freqtrade's own id for the trade.",
    "ft_order_id": "freqtrade's own id for the order.",
    "exchange_order_id": "The exchange's id for the order. This is what verification matches on.",
    "rejection_stage": "Where a rejection happened: the strategy, the bot, a risk rule, execution, the exchange, or the system.",
    "rejection_code": "The reason code for a rejection. Each code has a plain-language meaning.",
    "payload": "The details of this event, exactly as captured.",
}


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _select(db, table: str, **kwargs) -> tuple[list[dict], str]:
    """A read that answers empty, with a note, when the table is not there yet."""
    try:
        return db.select(table, **kwargs), ""
    except Exception as exc:  # noqa: BLE001 - new tables, old deployments
        log.info("learning: no %s yet: %s", table, exc)
        return [], f"{table} is not readable yet ({str(exc)[:120]})"


def outcome_of(decision: dict, events: list[dict]) -> dict:
    """What became of a decision, read off its events."""
    types_seen = [e.get("event_type") for e in events]
    rejection = next((e for e in events if e.get("event_type") in ("signal_rejected", "order_rejected")), None)
    cancelled = next((e for e in events if e.get("event_type") == "order_cancelled"), None)
    if decision.get("quarantined"):
        outcome = "quarantined"
    elif any(t in ("fill_completed", "position_opened", "position_closed", "position_adjusted") for t in types_seen):
        outcome = "executed"
    elif rejection is not None:
        outcome = "rejected"
    elif cancelled is not None:
        outcome = "cancelled"
    elif "order_acknowledged" in types_seen or "order_submitted" in types_seen:
        outcome = "placed"
    elif types_seen:
        outcome = "signalled"
    else:
        outcome = "signalled"
    reason = (rejection or cancelled or {}).get("rejection_code")
    return {
        "outcome": outcome,
        "outcome_meaning": OUTCOME_MEANING.get(outcome, ""),
        "reason": reason,
        "reason_meaning": REJECTION_MEANING.get(reason or "", "") if reason else "",
        "events": len(events),
        "last_event": max((e.get("event_time_utc") or "" for e in events), default=None),
        "headline": headline(decision, outcome, reason),
    }


def headline(decision: dict, outcome: str, reason: str | None) -> str:
    """One sentence a person can read without opening the record."""
    kind = decision.get("decision_kind") or "decision"
    intent = (decision.get("strategy_intent") or "").replace("_", " ")
    symbol = decision.get("symbol") or "?"
    candle = (decision.get("market_context") or {}).get("candle_open") or decision.get("decision_time_utc") or ""
    when = candle[11:16] if len(candle) >= 16 else candle
    verbs = {"entry": "enter", "exit": "leave", "add": "add to", "reduce": "reduce"}
    tag = decision.get("entry_tag") or decision.get("exit_reason")
    first = f"Wanted to {verbs.get(kind, kind)} {symbol} ({intent}) on the {when} candle"
    if tag:
        first += f", tagged {tag}"
    if outcome == "executed":
        second = "and did: the order filled."
    elif outcome == "placed":
        second = "and placed the order; it has not filled yet."
    elif outcome == "rejected":
        second = "but did not: " + (REJECTION_MEANING.get(reason or "", "") or "it was rejected.")
    elif outcome == "cancelled":
        second = "and placed an order that was cancelled before it filled."
    elif outcome == "quarantined":
        second = "but the record failed its consistency check and is kept aside."
    else:
        second = "and nothing has happened yet."
    return f"{first} {second}"


def _parse_feature_filters(specs: list[str]) -> list[tuple[str, str, float]]:
    parsed = []
    for spec in specs:
        parts = spec.split(":")
        if len(parts) != 3 or parts[1] not in ("gt", "gte", "lt", "lte", "eq", "ne"):
            raise HTTPException(status_code=400, detail=f"feature filter must look like rsi:gt:60, got {spec!r}")
        try:
            parsed.append((parts[0], parts[1], float(parts[2])))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"feature filter value must be a number: {spec!r}") from exc
    return parsed


def _feature_matches(snapshot: dict, filters: list[tuple[str, str, float]]) -> bool:
    for key, op, wanted in filters:
        value = (snapshot or {}).get(key)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            return False
        if not {"gt": value > wanted, "gte": value >= wanted, "lt": value < wanted, "lte": value <= wanted,
                "eq": value == wanted, "ne": value != wanted}[op]:
            return False
    return True


def timeline(events: list[dict]) -> list[dict]:
    """Events in time order, each with its stage and what it means."""
    ordered = sorted(events, key=lambda e: (e.get("event_time_utc") or "", e.get("event_id") or ""))
    return [{
        "stage": STAGE_OF.get(e.get("event_type") or "", "bot"),
        "event_type": e.get("event_type"),
        "event_time_utc": e.get("event_time_utc"),
        "meaning": EVENT_MEANING.get(e.get("event_type") or "", ""),
        "reason": e.get("rejection_code"),
        "reason_meaning": REJECTION_MEANING.get(e.get("rejection_code") or "", "") if e.get("rejection_code") else "",
        "event": e,
    } for e in ordered]


def explanations(record: dict) -> dict:
    """The glossary entries for the keys this record has."""
    return {key: FIELDS[key] for key in record if key in FIELDS}


@router.get("/glossary")
async def glossary() -> dict:
    return {
        "fields": FIELDS,
        "events": EVENT_MEANING,
        "stages": STAGE_ORDER,
        "kinds": KIND_MEANING,
        "intents": INTENT_MEANING,
        "outcomes": OUTCOME_MEANING,
        "rejections": REJECTION_MEANING,
    }


@router.get("/decisions")
async def decisions(
    db: UserDB,
    days: int = 7,
    pair: str = "",
    kind: str = "",
    intent: str = "",
    strategy: str = "",
    outcome: str = "",
    feature: Annotated[list[str] | None, Query()] = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """Decisions in a window, newest first, each with its outcome and a headline."""
    if days < 1 or days > 365:
        raise HTTPException(status_code=400, detail="days must be between 1 and 365")
    limit = max(1, min(limit, PAGE_LIMIT))
    offset = max(0, offset)
    feature_filters = _parse_feature_filters(feature or [])

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    filters = {"decision_time_utc": f"gte.{since}"}
    if pair:
        filters["symbol"] = f"eq.{pair}"
    if kind:
        filters["decision_kind"] = f"eq.{kind}"
    if intent:
        filters["strategy_intent"] = f"eq.{intent}"
    if strategy:
        filters["strategy_id"] = f"eq.{strategy}"

    rows, note = _select(db, "trading_decisions", filters=filters, order="decision_time_utc.desc",
                         limit=SCAN_LIMIT)
    events, events_note = _select(db, "trading_events",
                                  filters={"event_time_utc": f"gte.{since}"},
                                  order="event_time_utc.asc", limit=5000)
    by_decision: dict[str, list[dict]] = {}
    for event in events:
        if event.get("decision_id"):
            by_decision.setdefault(event["decision_id"], []).append(event)

    scanned = len(rows)
    if feature_filters:
        rows = [r for r in rows if _feature_matches(r.get("feature_snapshot") or {}, feature_filters)]
    items = []
    for row in rows:
        summary = outcome_of(row, by_decision.get(row.get("decision_id"), []))
        if outcome and summary["outcome"] != outcome:
            continue
        items.append({"decision": row, "summary": summary})

    counts: dict[str, int] = {}
    for item in items:
        counts[item["summary"]["outcome"]] = counts.get(item["summary"]["outcome"], 0) + 1

    page = items[offset:offset + limit]
    if not note and not rows:
        note = ("No decisions recorded in this window. The bot records one the moment its "
                "strategy signals; with LEARNING_ENABLED off nothing is recorded at all.")
    return {
        "items": page,
        "total": len(items),
        "counts": counts,
        "days": days,
        "scanned": scanned,
        "scan_capped": scanned >= SCAN_LIMIT,
        "note": note or events_note,
    }


@router.get("/decisions/{decision_id}")
async def decision(db: UserDB, decision_id: str) -> dict:
    """One decision in full: the raw record, its events as a timeline, and what every field means."""
    rows, note = _select(db, "trading_decisions", filters={"decision_id": f"eq.{decision_id}"}, limit=1)
    if not rows:
        raise HTTPException(status_code=404, detail=note or "no such decision")
    record = rows[0]
    events, events_note = _select(db, "trading_events", filters={"decision_id": f"eq.{decision_id}"},
                                  order="event_time_utc.asc", limit=500)
    return {
        "decision": record,
        "summary": outcome_of(record, events),
        "timeline": timeline(events),
        "events": events,
        "explanations": {
            "decision": explanations(record),
            "event": {key: FIELDS[key] for key in FIELDS if key in (events[0] if events else {})},
        },
        "note": events_note,
    }


@router.get("/positions/{position_id}")
async def position(db: UserDB, position_id: str) -> dict:
    """Everything decided about one position, entry to exit."""
    rows, note = _select(db, "trading_decisions", filters={"position_id": f"eq.{position_id}"},
                         order="decision_time_utc.asc", limit=200)
    events, events_note = _select(db, "trading_events", filters={"position_id": f"eq.{position_id}"},
                                  order="event_time_utc.asc", limit=2000)
    by_decision: dict[str, list[dict]] = {}
    for event in events:
        by_decision.setdefault(event.get("decision_id") or "", []).append(event)
    return {
        "position_id": position_id,
        "decisions": [{"decision": r, "summary": outcome_of(r, by_decision.get(r.get("decision_id"), []))}
                      for r in rows],
        "timeline": timeline(events),
        "note": note or events_note,
    }


HEALTH_MEANING = {
    "bot_instance_id": "Which bot this row is about.",
    "owner_id": "The account the bot belongs to.",
    "enabled": "Whether the Learning Module is switched on for this bot.",
    "decisions_24h": "Decisions recorded in the last day. Zero on a quiet market is normal; zero for days while the strategy signals is not.",
    "events_24h": "Events recorded in the last day: signals, orders, fills, positions.",
    "quarantined_24h": "Decisions kept aside because their record failed its own check. Should be zero.",
    "events_without_decision_24h": "Order or position events that could not be tied to a decision. Should be zero.",
    "events_orphaned_24h": "Events that point at a decision the database does not have. Should be zero; anything else is a bug in the adapter.",
    "decisions_without_events_24h": "Decisions with nothing attached yet. A few young ones are normal.",
    "outbox_pending": "Records waiting on the bot to be shipped to the database. Should be near zero.",
    "outbox_oldest_age_seconds": "How long the oldest waiting record has been waiting.",
    "outbox_quarantined": "Records the database refused ten times. Should be zero.",
    "written_total": "Records shipped since the bot started.",
    "failed_total": "Records the database refused, counting retries.",
    "retried_total": "Records that were retried after a refusal.",
    "dropped_total": "Records lost because the bot's local queue itself failed. Should be zero.",
    "outages_total": "Times the bot could not reach the database at all and paused.",
    "decisions_opened": "Decisions the adapter opened since the bot started.",
    "decisions_deduped": "Times a decision was seen again on a later pass and folded into the same record.",
    "rejections": "How often each rejection reason was seen, counting repeats.",
    "last_success_at": "When the writer last shipped a batch.",
    "last_decision_at": "When the newest decision was recorded.",
    "last_error": "The writer's most recent failure, if any.",
    "reported_at": "When the bot last published this row. Older than a few minutes means the bot is down or the module is off.",
    "adapter_version": "The version of the adapter that recorded these rows.",
}


@router.get("/health")
async def health(db: UserDB) -> dict:
    """Is the pipeline working: one row per bot, with every number explained."""
    rows, note = _select(db, "v_learning_health", order="reported_at.desc", limit=20)
    return {
        "bots": rows,
        "meaning": HEALTH_MEANING,
        "note": note or ("" if rows else "No bot has published learning status yet. The bot publishes "
                                        "once a minute while LEARNING_ENABLED is on."),
    }
