"""The three records of one intent, lined up against each other.

    the strategy said  ->  the bot instructed  ->  the exchange filled

Each arrow can break, and each break means something different:

  * A signal with no order is the bot declining or unable to act -- every slot
    full, not enough stake, the pair locked after a loss, or the bot was down.
    Nothing about this is visible in an order log, because there is no order.
  * An order with no fill is the exchange disagreeing with the bot's record,
    which is what the existing reconciliation checks.

A fourth column can join later: an independently written Pine version of the
same strategy, reporting through a webhook. It is worth being precise about
what that would mean. The exchange is definitionally right about its own fills,
so a mismatch there is the bot being wrong. TradingView is not right -- it is
merely independent, so a mismatch there means two implementations disagree and
somebody has to look. Presenting the second as though it were the first would
train anyone reading this page to ignore it.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException

from app.api.deps import UserDB

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/verification", tags=["verification"])

#: How close an order has to be to a signal to count as acting on it. One bar is
#: too tight -- freqtrade places on the next candle after the signal closes --
#: and anything much wider starts matching a signal to an unrelated trade.
MATCH_WINDOW_BARS = 3

#: Minutes per timeframe, for turning that bar count into a real interval.
TIMEFRAME_MINUTES = {
    "1m": 1, "3m": 3, "5m": 5, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240, "6h": 360, "8h": 480, "12h": 720,
    "1d": 1440, "3d": 4320, "1w": 10080,
}


def _when(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _side_of(order: dict) -> str:
    """An order's side in the strategy's vocabulary."""
    side = (order.get("side") or "").lower()
    return "enter_long" if side == "buy" else "exit_long" if side == "sell" else side


@router.get("/chain")
async def chain(db: UserDB, days: int = 7, pair: str = "") -> dict:
    """Signals, orders and fills for a window, matched up bar by bar."""
    if days < 1 or days > 90:
        raise HTTPException(status_code=400, detail="days must be between 1 and 90")

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    signal_filters = {"bar_time": f"gte.{since}"}
    order_filters = {"order_date": f"gte.{since}"}
    if pair:
        signal_filters["pair"] = f"eq.{pair}"
        order_filters["pair"] = f"eq.{pair}"

    try:
        signals = db.select("strategy_signals",
                            columns="source,strategy,pair,timeframe,side,bar_time,price",
                            filters=signal_filters, order="bar_time.desc", limit=1000)
    except Exception as exc:  # noqa: BLE001 - the table is new; an old bot has not written to it
        log.info("no strategy signals yet: %s", exc)
        signals = []

    try:
        orders = db.select("v_live_orders",
                           columns="ft_order_id,ft_trade_id,pair,exchange_order_id,status,"
                                   "side,price,average,amount,filled,order_date",
                           filters=order_filters, order="order_date.desc", limit=1000)
    except Exception as exc:  # noqa: BLE001 - the view exists only after first connect
        log.info("no live orders yet: %s", exc)
        orders = []

    # The exchange's verdict, as the last reconciliation recorded it. Read rather
    # than re-fetched: this service holds no exchange keys, by design.
    verdicts: dict[str, dict] = {}
    try:
        for row in db.select("order_reconciliations",
                             columns="exchange_order_id,matched,discrepancy_kind,notes,checked_at",
                             order="checked_at.desc", limit=1000):
            key = str(row.get("exchange_order_id") or "")
            if key and key not in verdicts:      # newest wins
                verdicts[key] = row
    except Exception as exc:  # noqa: BLE001
        log.info("no reconciliation yet: %s", exc)

    unclaimed = list(orders)
    rows = []

    for signal in signals:
        if signal.get("source") != "bot":
            continue
        bar = _when(signal.get("bar_time"))
        minutes = TIMEFRAME_MINUTES.get(signal.get("timeframe") or "", 5)
        window = timedelta(minutes=minutes * MATCH_WINDOW_BARS)

        acted = None
        for order in unclaimed:
            placed = _when(order.get("order_date"))
            if (order.get("pair") == signal.get("pair")
                    and _side_of(order) == signal.get("side")
                    and bar and placed and bar <= placed <= bar + window):
                acted = order
                break
        if acted is not None:
            unclaimed.remove(acted)

        verdict = verdicts.get(str(acted.get("exchange_order_id") or "")) if acted else None
        rows.append({
            "bar_time": signal.get("bar_time"),
            "pair": signal.get("pair"),
            "side": signal.get("side"),
            "timeframe": signal.get("timeframe"),
            "signal_price": signal.get("price"),
            "acted": acted is not None,
            "order": acted,
            "exchange": verdict,
            "outcome": _outcome(acted, verdict),
        })

    # Orders with no signal behind them: a force-exit, a stoploss the dataframe
    # does not carry, or a trade nobody here can account for.
    for order in unclaimed:
        verdict = verdicts.get(str(order.get("exchange_order_id") or ""))
        rows.append({
            "bar_time": order.get("order_date"),
            "pair": order.get("pair"),
            "side": _side_of(order),
            "timeframe": None,
            "signal_price": None,
            "acted": True,
            "order": order,
            "exchange": verdict,
            "outcome": "order_without_signal",
        })

    rows.sort(key=lambda r: str(r.get("bar_time") or ""), reverse=True)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["outcome"]] = counts.get(row["outcome"], 0) + 1

    return {
        "rows": rows,
        "counts": counts,
        "days": days,
        "signals_recorded": len(signals),
        "note": (
            "No signals recorded yet. The bot writes these on its own check "
            "cycle; give it a few minutes after a deploy."
            if not signals else ""
        ),
    }


#: What each combination of the three records means, in one word the UI can
#: colour and a human can act on.
def _outcome(order: dict | None, verdict: dict | None) -> str:
    if order is None:
        # The interesting one, and the one that had nowhere to appear before.
        return "signal_not_acted"
    if verdict is None:
        return "acted_unverified"
    if verdict.get("matched"):
        return "confirmed"
    return "exchange_disagrees"
