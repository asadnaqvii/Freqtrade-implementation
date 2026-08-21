"""What the strategy said, recorded before anything could get in the way.

Verification compares three records of the same intent:

    the strategy said   ->   the bot instructed   ->   the exchange filled

The second and third have been compared since the beginning. The first was
never written down anywhere, so the most interesting gap -- a signal the bot
never acted on -- left no trace at all. Every slot full, not enough stake, the
pair locked after a loss, the bot mid-deploy: all of those produce no order,
and a strategy that cannot trade looks exactly like a strategy with nothing to
say.

The signals come from the running bot's own analysed dataframe, which is the
same source the candle chart draws its triangles from. That matters: it is not
a re-derivation that might disagree for uninteresting reasons, it is the very
computation the bot acted on.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: Dataframe columns that mean "signal", mapped to what we call them. freqtrade
#: renamed buy/sell to enter_long/exit_long; strategies in the wild emit either.
SIGNAL_COLUMNS = {
    "enter_long": "enter_long",
    "exit_long": "exit_long",
    "enter_short": "enter_short",
    "exit_short": "exit_short",
    "buy": "enter_long",
    "sell": "exit_long",
}

#: How far back to record. The dataframe the bot holds is longer than this, but
#: re-storing months of history on every pass is a lot of writes to say nothing
#: new; the unique constraint makes the overlap harmless.
DEFAULT_LOOKBACK_HOURS = 48


def _bar_times(payload: dict[str, Any]) -> tuple[list[Any], str]:
    """Extract the timestamp column, preferring the epoch one freqtrade adds."""
    columns = payload.get("columns") or []
    for name in ("__date_ts", "date"):
        if name in columns:
            return columns, name
    return columns, ""


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, (int, float)):
        # freqtrade's __date_ts is epoch milliseconds.
        return datetime.fromtimestamp(value / 1000, tz=timezone.utc)
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def extract(payload: dict[str, Any], *, lookback_hours: int = DEFAULT_LOOKBACK_HOURS,
            now: datetime | None = None) -> list[dict[str, Any]]:
    """Every signal in one pair_candles payload, newest-relevant first.

    Returns plain dicts rather than writing anything, so the shape is testable
    without a database and the caller decides what to do with them.
    """
    rows = payload.get("data") or []
    columns, date_col = _bar_times(payload)
    if not rows or not date_col:
        return []

    index = {name: i for i, name in enumerate(columns)}
    date_at = index[date_col]
    close_at = index.get("close")
    present = {col: index[col] for col in SIGNAL_COLUMNS if col in index}
    if not present:
        return []

    cutoff = (now or datetime.now(timezone.utc)) - timedelta(hours=lookback_hours)
    pair = payload.get("pair") or ""
    timeframe = payload.get("timeframe") or ""
    out: list[dict[str, Any]] = []

    for row in rows:
        if not isinstance(row, list) or date_at >= len(row):
            continue
        bar = _as_utc(row[date_at])
        if bar is None or bar < cutoff:
            continue
        for column, side in ((c, SIGNAL_COLUMNS[c]) for c in present):
            raw = row[present[column]]
            try:
                fired = bool(raw) and float(raw) != 0.0
            except (TypeError, ValueError):
                fired = bool(raw)
            if not fired:
                continue
            out.append({
                "source": "bot",
                "strategy": payload.get("strategy"),
                "pair": pair,
                "timeframe": timeframe,
                "side": side,
                "bar_time": bar.isoformat(),
                "price": float(row[close_at]) if close_at is not None
                         and close_at < len(row) and row[close_at] is not None else None,
                "payload": {"column": column},
            })
    return out


def record(client, *, bot, pairs: Iterable[str], timeframe: str,
           owner_id: str | None, bot_instance_id: str | None,
           exchange: str | None = None, limit: int = 500,
           lookback_hours: int = DEFAULT_LOOKBACK_HOURS) -> int:
    """Read each pair's dataframe from the bot and store the signals in it.

    Best effort per pair: one unreadable pair must not cost the others. Written
    with on_conflict so re-reading an overlapping window is a no-op rather than
    a duplicate -- which is what makes it safe to call on every check.
    """
    stored = 0
    for pair in pairs:
        try:
            payload = bot.get("pair_candles", {
                "pair": pair, "timeframe": timeframe, "limit": limit,
            })
        except Exception as exc:  # noqa: BLE001
            log.info("no dataframe for %s: %s", pair, exc)
            continue

        signals = extract(payload if isinstance(payload, dict) else {},
                          lookback_hours=lookback_hours)
        if not signals:
            continue

        rows = [{**s, "owner_id": owner_id, "bot_instance_id": bot_instance_id,
                 "exchange": exchange} for s in signals]
        try:
            client.upsert(
                "strategy_signals", rows,
                on_conflict="bot_instance_id,source,pair,timeframe,side,bar_time",
            )
            stored += len(rows)
        except Exception as exc:  # noqa: BLE001 - never stop the bot trading
            log.warning("could not store signals for %s: %s", pair, exc)
    return stored
