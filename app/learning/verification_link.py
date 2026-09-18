"""Phase 5: verification, referenced from the decision it checked.

Reconciliation already asks the exchange what it did with every order the
bot recorded, and writes the answers to order_reconciliations. This adds
one thing: the same verdicts as events on the decisions that placed those
orders, so a decision's timeline ends with what the exchange says happened
rather than with what the bot believed. Verification itself is untouched --
this reads its findings after they are persisted and writes trading_events
directly, off the trading path, with the service client the self-check
already holds.

An order the exchange reports and no decision explains is recorded as
`unaccounted_exchange_activity` with no decision id. Never a made-up one.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Sequence

from app.learning.contracts import TradingEvent
from app.learning.ids import new_event_id
from app.learning.keys import event_key

log = logging.getLogger(__name__)

#: How many order ids to look up per request. PostgREST filters travel in the URL.
LOOKUP_CHUNK = 80
#: How many events to ship per request.
WRITE_CHUNK = 200

#: Discrepancy kinds that mean the exchange has an order the bot never recorded.
UNACCOUNTED_KINDS = {"unknown_order", "not_recorded", "extra_on_exchange", "unaccounted"}


def _quoted_in(values: Sequence[str]) -> str:
    return "in.(" + ",".join('"' + v.replace('"', '') + '"' for v in values) + ")"


def _lookup(client, order_ids: list[str]) -> dict[str, dict]:
    """exchange_order_id -> the decision that placed it (from events already recorded)."""
    found: dict[str, dict] = {}
    for start in range(0, len(order_ids), LOOKUP_CHUNK):
        chunk = order_ids[start:start + LOOKUP_CHUNK]
        rows = client.select(
            "trading_events", columns="exchange_order_id,decision_id,position_id,symbol,ft_trade_id",
            filters={"exchange_order_id": _quoted_in(chunk), "decision_id": "not.is.null"},
            limit=1000,
        )
        for row in rows:
            found.setdefault(str(row.get("exchange_order_id")), row)
    return found


def _rows(findings: Sequence[Any], run_id: str, bot_instance_id: str | None, account_id: str | None) -> list[dict]:
    rows = []
    for finding in findings:
        if hasattr(finding, "as_row"):
            rows.append(finding.as_row(run_id, bot_instance_id, account_id))
        elif isinstance(finding, dict):
            rows.append(finding)
    return rows


def record_reconciliation(client, *, run_id: str, bot_instance_id: str | None, owner_id: str | None,
                          findings: Sequence[Any], account_id: str | None = None,
                          checked_at: datetime | None = None) -> int:
    """Write the verdicts of one reconciliation run as events. Returns how many were sent.

    Idempotent: the same run recorded twice produces the same keys, and the
    tables ignore duplicates.
    """
    when = checked_at or datetime.now(timezone.utc)
    rows = _rows(findings, run_id, bot_instance_id, account_id)
    order_ids = sorted({str(r["exchange_order_id"]) for r in rows if r.get("exchange_order_id")})
    decisions = _lookup(client, order_ids) if order_ids else {}

    matched = sum(1 for r in rows if r.get("matched"))
    events = [TradingEvent(
        event_id=new_event_id(), event_type="verification_reference", event_time_utc=when.isoformat(),
        event_source="verifier", decision_id=None, owner_id=owner_id, bot_instance_id=bot_instance_id,
        idempotency_key=event_key(decision_id=None, event_type="verification_reference", event_time=when,
                                  order_ref=str(run_id), bucket_seconds=86400),
        payload={"run_id": run_id, "findings": len(rows), "matched": matched, "disputed": len(rows) - matched},
    ).to_row()]

    for row in rows:
        order_id = str(row.get("exchange_order_id") or "")
        link = decisions.get(order_id)
        kind = row.get("discrepancy_kind")
        if link is None and (kind in UNACCOUNTED_KINDS or not row.get("ft_order_id")):
            event_type = "unaccounted_exchange_activity"
        else:
            event_type = "verification_status_changed"
        verdict = "matched" if row.get("matched") else str(kind or "disputed")
        events.append(TradingEvent(
            event_id=new_event_id(), event_type=event_type, event_time_utc=when.isoformat(),
            event_source="verifier", decision_id=link.get("decision_id") if link else None,
            owner_id=owner_id, bot_instance_id=bot_instance_id, symbol=row.get("pair") or (link or {}).get("symbol"),
            position_id=(link or {}).get("position_id"),
            ft_trade_id=(link or {}).get("ft_trade_id"),
            exchange_order_id=order_id or None,
            idempotency_key=event_key(decision_id=(link or {}).get("decision_id"), event_type=event_type,
                                      event_time=when, order_ref=order_id or str(row.get("ft_order_id") or row.get("pair") or ""),
                                      discriminator=f"{run_id}:{verdict}", bucket_seconds=86400),
            payload={"run_id": run_id, "matched": bool(row.get("matched")), "verdict": verdict,
                     "discrepancy_kind": kind, "discrepancy_pct": row.get("discrepancy_pct"),
                     "notes": row.get("notes"), "pair": row.get("pair"), "ft_order_id": row.get("ft_order_id")},
        ).to_row())

    for start in range(0, len(events), WRITE_CHUNK):
        client.insert_new_only("trading_events", events[start:start + WRITE_CHUNK], on_conflict="idempotency_key")
    log.info("linked reconciliation %s to %s decision(s): %s event(s)", run_id, len(decisions), len(events))
    return len(events)
