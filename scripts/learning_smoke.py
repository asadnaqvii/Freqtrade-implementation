#!/usr/bin/env python3
"""Exercise the Learning Module's writer against a real Supabase project.

The synthetic source for the soak. It queues a made-up decision and the
events that would follow it into a throwaway outbox, ships them with the
real writer through the real client, ships the same records again from a
fresh outbox to prove a replay is free, then reads back what landed and
prints the writer's health.

Nothing it writes could be mistaken for a real decision: the environment is
"smoke", the symbol is SMOKE/USDT, and the strategy is SmokeStrategy. The
rows have no owner, so no dashboard shows them; prune_learning_records()
takes them out with everything else past retention.

    SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... \
        python scripts/learning_smoke.py --environment staging
    python scripts/learning_smoke.py --environment staging --rounds 5

Exit code 0 when every row landed and the replay wrote nothing new, 1
otherwise. It refuses to run without --environment staging or local: these
rows are fake, and the tables are append-only.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.supabase import SupabaseClient  # noqa: E402
from app.learning.contracts import TradingDecision, TradingEvent, now_iso  # noqa: E402
from app.learning.ids import new_decision_id, new_event_id, new_position_id  # noqa: E402
from app.learning.keys import decision_key, event_key  # noqa: E402
from app.learning.outbox import SqliteOutbox  # noqa: E402
from app.learning.provenance import ADAPTER_VERSION  # noqa: E402
from app.learning.writer import LearningWriter  # noqa: E402

CHAIN = ("signal_generated", "order_submitted", "order_acknowledged", "fill_completed", "position_opened")


def synthetic_records(environment: str, candle_open: datetime) -> tuple[dict, list[dict]]:
    """One decision and the five events a clean entry produces."""
    decided_at = candle_open + timedelta(seconds=5)
    decision_id = new_decision_id()
    position_id = new_position_id()
    decision = TradingDecision(
        decision_id=decision_id,
        decision_time_utc=decided_at.isoformat(),
        environment=environment,
        exchange="none",
        symbol="SMOKE/USDT",
        timeframe="4h",
        decision_kind="entry",
        strategy_intent="enter_long",
        strategy_id="SmokeStrategy",
        strategy_version="0",
        feature_set_version="smoke",
        position_id=position_id,
        market_data_max_ts=candle_open.isoformat(),
        market_context={"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 1000},
        feature_snapshot={"rsi": 61.2, "adx_falling": None, "ema_ok": True},
        portfolio_snapshot={"open_positions": 0, "free_slots": 6},
        risk_snapshot={"stoploss": -0.05, "locks": []},
        provenance={"source": "learning_smoke", "adapter_version": ADAPTER_VERSION},
        idempotency_key=decision_key(
            bot_instance_id=None, strategy_id="SmokeStrategy", strategy_code_hash="smoke",
            symbol="SMOKE/USDT", timeframe="4h", decision_kind="entry", strategy_intent="enter_long",
            candle_open=candle_open.isoformat(),
        ),
    )
    decision.validate()
    events = []
    for step, event_type in enumerate(CHAIN):
        at = decided_at + timedelta(seconds=step + 1)
        event = TradingEvent(
            event_id=new_event_id(),
            event_type=event_type,
            event_time_utc=at.isoformat(),
            event_source="learning_adapter",
            decision_id=decision_id,
            symbol="SMOKE/USDT",
            position_id=position_id,
            exchange_order_id="smoke-order" if step else None,
            payload={"step": step, "source": "learning_smoke"},
            idempotency_key=event_key(decision_id=decision_id, event_type=event_type, event_time=at,
                                      order_ref="smoke-order" if step else ""),
        )
        event.validate()
        events.append(event.to_row())
    return decision.to_row(), events


def ship(client, outbox_path: str, records: list[tuple[dict, list[dict]]]) -> LearningWriter:
    outbox = SqliteOutbox(outbox_path)
    try:
        for decision, events in records:
            outbox.enqueue("decision", decision["idempotency_key"], decision)
            for event in events:
                outbox.enqueue("event", event["idempotency_key"], event)
        writer = LearningWriter(outbox, client, interval=0.5)
        writer.flush(timeout=60)
        writer.health_snapshot = writer.health()  # type: ignore[attr-defined]
        return writer
    finally:
        outbox.close()


def landed(client, decision_key_value: str) -> tuple[int, int]:
    decisions = client.select("trading_decisions", columns="decision_id",
                              filters={"idempotency_key": f"eq.{decision_key_value}"})
    if not decisions:
        return 0, 0
    events = client.select("trading_events", columns="event_id",
                           filters={"decision_id": f"eq.{decisions[0]['decision_id']}"})
    return len(decisions), len(events)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--environment", choices=("staging", "local"), required=True,
                        help="which environment these fake rows belong to; production is refused")
    parser.add_argument("--rounds", type=int, default=1, help="how many decisions to send")
    parser.add_argument("--outbox", default=None, help="outbox file to use (default: a temporary one)")
    args = parser.parse_args()

    if os.environ.get("ENVIRONMENT", "").strip().lower() == "production":
        print("refusing: ENVIRONMENT is production, and these rows are fake", file=sys.stderr)
        return 1

    client = SupabaseClient.service()
    base = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    records = [synthetic_records(args.environment, base - timedelta(hours=4 * i)) for i in range(args.rounds)]

    workdir = Path(args.outbox).parent if args.outbox else Path(tempfile.mkdtemp(prefix="learning_smoke_"))
    first = ship(client, args.outbox or str(workdir / "first.sqlite"), records)
    replay = ship(client, str(workdir / "replay.sqlite"), records)

    ok = True
    for decision, events in records:
        d_count, e_count = landed(client, decision["idempotency_key"])
        fine = d_count == 1 and e_count == len(events)
        ok &= fine
        print(f"{'ok ' if fine else 'BAD'} decision {decision['decision_id']}: "
              f"{d_count} decision row, {e_count}/{len(events)} events")

    expected = args.rounds * (1 + len(CHAIN))
    for label, writer in (("first pass", first), ("replay", replay)):
        health = writer.health_snapshot  # type: ignore[attr-defined]
        shipped = health["written_total"] == expected and health["outbox_pending"] == 0
        ok &= shipped
        print(f"{'ok ' if shipped else 'BAD'} {label}: {json.dumps(health, default=str)}")
    print("PASS" if ok else "FAIL", f"({now_iso()})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
