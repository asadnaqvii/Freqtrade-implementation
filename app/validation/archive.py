"""Copy closed trades out of freqtrade's tables into the durable archive.

freqtrade owns ft_main and treats it as its own working store: it is recreated
on a schema reset and it was cleared outright during the dry-run-to-live
cutover. Anything that lives only there is one operational decision away from
being gone, which is exactly what happened to five days of live trades having
no archived copy.

public.trade_archive is the copy that outlives all of that. It already held the
Railway history, imported once by hand; nothing was keeping it current, so it
recorded a bot that no longer exists and none of the one that does.

Deliberately closed trades only. An open position's profit moves with the
market, so archiving one stores a number that is wrong by the time it is read;
it lands here when it closes and its result is final.
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
    """Upsert every closed trade the bot has into the archive.

    Keyed on (bot_instance_id, ft_trade_id), so re-running is a no-op for
    trades already stored and an update for ones whose final numbers arrived
    late. Returns how many rows were written.
    """
    if not bot_instance_id:
        return 0

    try:
        rows = client.select("v_live_trades", columns="*",
                             filters={"is_open": "is.false"},
                             order="close_date.desc", limit=limit)
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
