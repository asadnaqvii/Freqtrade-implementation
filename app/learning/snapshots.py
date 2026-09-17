"""What a decision looked at, taken from what freqtrade already holds.

Nothing here talks to an exchange or a database on its own. A caller hands
in the analysed candle row, the wallet object and a way to list open trades,
and gets back the four snapshots a decision record carries: the candle
(market context), the indicator values (features), what the bot held
(portfolio) and what constrained it (risk). Every piece is taken separately
and a piece that cannot be read is written down as unavailable, so one bad
read never costs the whole record.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from app.learning.canonical import canonical_json, sanitise

OHLCV = ("date", "open", "high", "low", "close", "volume")

#: Columns that carry the strategy's verdict rather than its evidence.
SIGNAL_COLUMNS = ("enter_long", "exit_long", "enter_short", "exit_short",
                  "enter_tag", "exit_tag", "buy", "sell", "buy_tag", "sell_tag")

#: A feature snapshot is evidence, not a data lake: enough for any strategy
#: this platform runs, small enough to index.
MAX_FEATURE_KEYS = 200
MAX_FEATURE_BYTES = 64 * 1024

#: freqtrade treats a candle older than two timeframes plus this as outdated
#: and refuses to act on it (exchange.outdated_offset, default 5 minutes).
OUTDATED_OFFSET_MINUTES = 5

_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def timeframe_seconds(timeframe: str) -> int:
    """'4h' -> 14400. Raises ValueError for anything that is not <int><unit>."""
    text = (timeframe or "").strip().lower()
    if len(text) < 2 or text[-1] not in _UNITS or not text[:-1].isdigit():
        raise ValueError(f"not a timeframe: {timeframe!r}")
    return int(text[:-1]) * _UNITS[text[-1]]


def last_row(dataframe: Any) -> dict | None:
    """The last row of an analysed dataframe as a plain dict, or None."""
    try:
        if dataframe is None or len(dataframe) == 0:
            return None
        if hasattr(dataframe, "iloc"):
            return dict(dataframe.iloc[-1].to_dict())
        if isinstance(dataframe, dict):
            return dict(dataframe)
        return dict(dataframe[-1])
    except Exception:  # noqa: BLE001 - a row we cannot read is no row
        return None


def as_datetime(value: Any) -> datetime | None:
    """A candle's date column, whatever pandas made of it, as an aware UTC datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        stamp = value
    else:
        text = sanitise(value)
        if not isinstance(text, str):
            return None
        try:
            stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return stamp.astimezone(timezone.utc)


def candle_open(row: dict | None) -> datetime | None:
    return as_datetime(row.get("date")) if row else None


def candle_close(opened: datetime, timeframe: str) -> datetime:
    return opened + timedelta(seconds=timeframe_seconds(timeframe))


def floor_to_candle(when: datetime, timeframe: str) -> datetime:
    """The open of the candle `when` falls in."""
    seconds = timeframe_seconds(timeframe)
    epoch = int(when.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, tz=timezone.utc)


def is_outdated(opened: datetime, timeframe: str, now: datetime) -> bool:
    """Would freqtrade refuse to act on a candle that opened then? (Its own rule.)"""
    limit = timedelta(seconds=2 * timeframe_seconds(timeframe), minutes=OUTDATED_OFFSET_MINUTES)
    return opened < now - limit


def _is_set(value: Any) -> bool:
    try:
        return value is not None and not (isinstance(value, float) and math.isnan(value)) and int(value) == 1
    except (TypeError, ValueError):
        return False


def wants_entry(row: dict | None) -> str | None:
    """The intent the strategy put on the row, by freqtrade's own reading.

    A long entry that is contradicted by an exit or a short on the same row is
    no entry -- `IStrategy.get_entry_signal` says so, and this mirrors it.
    """
    if not row:
        return None
    enter_long = _is_set(row.get("enter_long", row.get("buy")))
    exit_long = _is_set(row.get("exit_long", row.get("sell")))
    enter_short = _is_set(row.get("enter_short"))
    exit_short = _is_set(row.get("exit_short"))
    if enter_long and not (exit_long or enter_short):
        return "enter_long"
    if enter_short and not (exit_short or enter_long):
        return "enter_short"
    return None


def wants_exit(row: dict | None, is_short: bool = False) -> bool:
    if not row:
        return False
    return _is_set(row.get("exit_short")) if is_short else _is_set(row.get("exit_long", row.get("sell")))


def split_row(row: dict | None, timeframe: str) -> tuple[dict, dict, dict]:
    """(market_context, feature_snapshot, signals) from one analysed row."""
    if not row:
        return {}, {}, {}
    opened = candle_open(row)
    market = {
        "timeframe": timeframe,
        "candle_open": opened.isoformat() if opened else None,
        "candle_close": candle_close(opened, timeframe).isoformat() if opened else None,
    }
    for name in OHLCV[1:]:
        if name in row:
            market[name] = sanitise(row[name])
    signals = {name: sanitise(row[name]) for name in SIGNAL_COLUMNS if name in row}
    features = {}
    for name in sorted(k for k in row if k not in OHLCV and k not in SIGNAL_COLUMNS):
        features[str(name)] = sanitise(row[name])
    return market, cap_features(features), signals


def cap_features(features: dict) -> dict:
    """Bound the snapshot by key count and by size, and say when it was cut."""
    total = len(features)
    if total > MAX_FEATURE_KEYS:
        features = dict(list(features.items())[:MAX_FEATURE_KEYS])
    while features and len(canonical_json(features).encode("utf-8")) > MAX_FEATURE_BYTES:
        features.pop(next(reversed(features)))
    if len(features) < total:
        features["_truncated"] = total - len(features)
    return features


def _piece(label: str, read: Callable[[], Any], into: dict) -> None:
    try:
        into[label] = sanitise(read())
    except Exception as exc:  # noqa: BLE001 - one unreadable piece, not a lost record
        into.setdefault("_unavailable", {})[label] = str(exc)[:200]


def portfolio_snapshot(*, wallets: Any, open_trades: Callable[[], list], stake_currency: str) -> dict:
    """What the bot held and could spend, once per decision."""
    snapshot: dict = {"stake_currency": stake_currency}
    _piece("total_stake", lambda: wallets.get_total_stake_amount(), snapshot)
    _piece("available_stake", lambda: wallets.get_available_stake_amount(), snapshot)

    def positions():
        rows = []
        for trade in open_trades():
            rows.append({
                "ft_trade_id": getattr(trade, "id", None),
                "pair": getattr(trade, "pair", None),
                "is_short": bool(getattr(trade, "is_short", False)),
                "amount": getattr(trade, "amount", None),
                "open_rate": getattr(trade, "open_rate", None),
                "stake_amount": getattr(trade, "stake_amount", None),
                "open_date": getattr(trade, "open_date_utc", None),
                "enter_tag": getattr(trade, "enter_tag", None),
            })
        return rows

    _piece("open_positions", positions, snapshot)
    positions_held = snapshot.get("open_positions")
    if isinstance(positions_held, list):
        snapshot["open_position_count"] = len(positions_held)
        snapshot["stake_in_positions"] = sum((p.get("stake_amount") or 0) for p in positions_held)
    return snapshot


def lock_info(lock: Any) -> dict:
    return {
        "pair": getattr(lock, "pair", None),
        "side": getattr(lock, "side", None),
        "reason": getattr(lock, "reason", None),
        "lock_end_time": sanitise(getattr(lock, "lock_end_time_utc", None)
                                  or getattr(lock, "lock_end_time", None)),
    }


def risk_snapshot(*, pair_locks: Callable[[], list], global_lock: Callable[[], bool],
                  max_open_trades: Any, free_slots: Callable[[], Any], stoploss: Any,
                  trailing_stop: Any, position_adjustment_enable: Any, protections: Any) -> dict:
    """What constrained the decision: locks, slots, the stop, the protections."""
    snapshot: dict = {
        "max_open_trades": sanitise(max_open_trades),
        "stoploss": sanitise(stoploss),
        "trailing_stop": sanitise(trailing_stop),
        "position_adjustment_enable": sanitise(position_adjustment_enable),
        "protections": sanitise(protections or []),
    }
    _piece("pair_locks", lambda: [lock_info(lock) for lock in pair_locks()], snapshot)
    _piece("global_lock", global_lock, snapshot)
    _piece("free_slots", free_slots, snapshot)
    return snapshot
