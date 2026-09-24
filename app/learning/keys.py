"""Idempotency keys: one logical decision or event, one row, however many
times it is captured.

Load-bearing because freqtrade re-evaluates the same candle every five
seconds: the same `enter_long` row is seen ~2,880 times per 4h candle. A
decision is therefore keyed on the candle it was made on, not on when it was
noticed; an event on the decision, its type and a second-resolution bucket of
when it happened, plus whatever makes it distinct (an order id, a fill).
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.learning.canonical import sha256_text


def time_bucket(when: datetime | str, seconds: int = 1) -> str:
    """ISO-8601 of `when` floored to `seconds`. Accepts an ISO string too."""
    if isinstance(when, str):
        when = datetime.fromisoformat(when.replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    when = when.astimezone(timezone.utc)
    epoch = int(when.timestamp())
    floored = epoch - (epoch % max(int(seconds), 1))
    return datetime.fromtimestamp(floored, tz=timezone.utc).isoformat()


def decision_key(*, bot_instance_id: str | None, strategy_id: str, strategy_code_hash: str | None,
                 symbol: str, timeframe: str, decision_kind: str, strategy_intent: str,
                 candle_open: str, seq: int = 0) -> str:
    parts = ["d1", bot_instance_id or "", strategy_id, strategy_code_hash or "", symbol,
             timeframe, decision_kind, strategy_intent, candle_open, str(int(seq))]
    return sha256_text("|".join(parts))


def event_key(*, decision_id: str | None, event_type: str, event_time: datetime | str,
              order_ref: str = "", discriminator: str = "", bucket_seconds: int = 1) -> str:
    parts = ["e1", decision_id or "none", event_type,
             time_bucket(event_time, bucket_seconds), order_ref or "", discriminator or ""]
    return sha256_text("|".join(parts))
