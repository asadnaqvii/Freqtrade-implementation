"""Copy closed trades out of freqtrade's tables into the durable archive.

freqtrade owns ft_main and treats it as its own working store: it is recreated
on a schema reset and it was cleared outright during the dry-run-to-live
cutover. Anything that lives only there is one operational decision away from
being gone, which is exactly what happened to five days of live trades having
no archived copy.

public.trade_archive is the copy that outlives all of that. It already held the
Railway history, imported once by hand; nothing was keeping it current, so it
recorded a bot that no longer exists and none of the one that does.

Open positions are archived too. They were excluded on the reasoning that an
open position's profit moves with the market, so storing one stores a number
that is wrong by the time it is read. That reasoning does not survive contact
with the data: freqtrade leaves close_date, close_rate, close_profit_abs and
close_profit_pct NULL until a trade closes, so an open row carries no moving
number to go stale.

What the exclusion did cost was the one number the watchdog needs most. It
counts open positions from this table, so it always read zero, and the sentence
that turns an outage into urgency -- "6 position(s) open and unmanaged, no
stop-loss is being applied" -- could never fire. The count has to live here
precisely because it is wanted when the bot is unreachable, which is exactly
when its own live view cannot be read.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)

#: Columns carried across, mapped from what the live view calls them.
FIELDS = (
    ("ft_trade_id", "ft_trade_id"),
    ("pair", "pair"),
    ("base_currency", "base_currency"),
    ("quote_currency", "quote_currency"),
    ("exchange", "exchange"),
    ("strategy", "strategy"),
    ("timeframe", "timeframe"),
    ("is_short", "is_short"),
    ("is_open", "is_open"),
    ("amount", "amount"),
    ("stake_amount", "stake_amount"),
    ("open_rate", "open_rate"),
    ("close_rate", "close_rate"),
    ("open_date", "open_date"),
    ("close_date", "close_date"),
    ("close_profit_abs", "close_profit_abs"),
    ("close_profit_pct", "close_profit_pct"),
    ("realized_profit", "realized_profit"),
    ("fee_open", "fee_open"),
    ("fee_close", "fee_close"),
    ("enter_tag", "enter_tag"),
    ("exit_reason", "exit_reason"),
    ("leverage", "leverage"),
)


def sync(client, *, bot_instance_id: str | None, owner_id: str | None,
         trading_mode: str = "live", limit: int = 1000) -> int:
    """Upsert every trade the bot has into the archive, open ones included.

    Keyed on (bot_instance_id, ft_trade_id), so re-running is a no-op for
    trades already stored and an update for ones whose final numbers arrived
    late -- which is what a position closing looks like from here. Returns how
    many rows were written.
    """
    if not bot_instance_id:
        return 0

    try:
        # Ordered by open_date, not close_date: an open trade has no close date,
        # and ordering by a null column decides which rows the limit keeps.
        rows = client.select("v_live_trades", columns="*",
                             order="open_date.desc", limit=limit)
    except Exception as exc:  # noqa: BLE001 - the view exists only after first connect
        log.info("no live trade view to archive from: %s", exc)
        return 0

    payload: list[dict[str, Any]] = []
    for row in rows:
        record = {target: row.get(source) for target, source in FIELDS}
        if record.get("ft_trade_id") is None:
            continue
        record.update(bot_instance_id=bot_instance_id, owner_id=owner_id,
                      trading_mode=trading_mode)
        payload.append(record)

    if not payload:
        return 0

    try:
        client.upsert("trade_archive", payload,
                      on_conflict="bot_instance_id,ft_trade_id", returning=False)
    except Exception as exc:  # noqa: BLE001 - never stop the bot trading
        log.warning("could not archive trades: %s", exc)
        return 0
    return len(payload)
