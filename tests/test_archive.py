"""Keeping the durable copy of trades current.

freqtrade owns ft_main and treats it as a working store: it is recreated on a
schema reset and it was cleared outright during the dry-run-to-live cutover.
public.trade_archive is the copy meant to outlive that, and nothing was keeping
it current -- it held a bot that no longer exists and none of the one that does,
so five days of live trades had exactly one copy, in the table most likely to be
wiped.
"""

from __future__ import annotations

from app.validation import archive


class Client:
    def __init__(self, rows=None, select_boom=None, upsert_boom=None):
        self.rows = rows or []
        self.select_boom = select_boom
        self.upsert_boom = upsert_boom
        self.selects = []
        self.orders = []
        self.upserts = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        if self.select_boom:
            raise self.select_boom
        self.selects.append((table, dict(filters or {})))
        self.orders.append(order)
        return list(self.rows)

    def upsert(self, table, rows, *, on_conflict, returning=True):
        if self.upsert_boom:
            raise self.upsert_boom
        self.upserts.append((table, rows, on_conflict))
        return rows


def trade(**kw):
    base = {"ft_trade_id": 9, "pair": "XMR/USDT", "base_currency": "XMR",
            "quote_currency": "USDT", "exchange": "kucoin",
            "strategy": "TrendPullbackStrategy", "timeframe": "4h",
            "is_short": False, "is_open": False, "amount": 0.567,
            "stake_amount": 236.72, "open_rate": 417.51, "close_rate": 425.0,
            "open_date": "2026-08-20T09:01:36+00:00",
            "close_date": "2026-08-20T17:00:00+00:00",
            "close_profit_abs": 4.2, "close_profit_pct": 1.79,
            "realized_profit": 4.2, "fee_open": 0.001, "fee_close": 0.001,
            "enter_tag": "pullback", "exit_reason": "roi", "leverage": 1.0}
    return {**base, **kw}


def test_a_closed_trade_is_archived_with_its_strategy():
    c = Client(rows=[trade()])
    assert archive.sync(c, bot_instance_id="b1", owner_id="o1") == 1
    table, rows, conflict = c.upserts[0]
    assert table == "trade_archive"
    assert conflict == "bot_instance_id,ft_trade_id"
    assert rows[0]["strategy"] == "TrendPullbackStrategy", (
        "the strategy stamp is the whole point of archiving these"
    )
    assert rows[0]["bot_instance_id"] == "b1" and rows[0]["owner_id"] == "o1"


def test_open_positions_are_archived_too():
    """These were excluded, on the reasoning that an open position's profit
    moves with the market. It does not: freqtrade leaves the close_* columns
    NULL until a trade closes, so an open row carries no number to go stale.

    What the exclusion cost was the count the watchdog reads from this table.
    It was always zero, so the sentence that makes an outage urgent -- "6
    position(s) open and unmanaged" -- could never appear. The count has to be
    here because it is wanted exactly when the bot is unreachable and its own
    live view cannot be read."""
    c = Client(rows=[trade(), trade(ft_trade_id=10, is_open=True,
                                    close_date=None, close_rate=None,
                                    close_profit_abs=None, close_profit_pct=None)])
    archive.sync(c, bot_instance_id="b1", owner_id=None)
    _, filters = c.selects[0]
    assert filters.get("is_open") is None, "open positions must not be filtered out"
    assert {r["ft_trade_id"] for r in c.upserts[0][1]} == {9, 10}


def test_the_ordering_does_not_depend_on_a_null_column():
    """The order decides which rows survive the limit. Ordering by close_date
    sorted on a column every open trade leaves NULL."""
    c = Client(rows=[trade()])
    archive.sync(c, bot_instance_id="b1", owner_id=None)
    assert c.orders[0].startswith("open_date")


def test_re_running_is_an_upsert_not_a_duplicate():
    c = Client(rows=[trade()])
    archive.sync(c, bot_instance_id="b1", owner_id=None)
    archive.sync(c, bot_instance_id="b1", owner_id=None)
    assert all(u[2] == "bot_instance_id,ft_trade_id" for u in c.upserts), (
        "without the conflict key a second pass duplicates every trade"
    )


def test_the_trading_mode_is_recorded():
    """Dry-run and live trades must never be counted together as real P&L."""
    c = Client(rows=[trade()])
    archive.sync(c, bot_instance_id="b1", owner_id=None, trading_mode="dry_run")
    assert c.upserts[0][1][0]["trading_mode"] == "dry_run"


def test_a_trade_with_no_id_is_skipped_rather_than_written_without_a_key():
    c = Client(rows=[trade(ft_trade_id=None), trade(ft_trade_id=10)])
    assert archive.sync(c, bot_instance_id="b1", owner_id=None) == 1


def test_no_bot_means_nothing_to_key_on():
    c = Client(rows=[trade()])
    assert archive.sync(c, bot_instance_id=None, owner_id=None) == 0
    assert c.upserts == []


def test_a_missing_view_is_not_a_failure():
    """The view is created on the bot's first connect; before then there is
    genuinely nothing to archive."""
    c = Client(select_boom=RuntimeError("relation v_live_trades does not exist"))
    assert archive.sync(c, bot_instance_id="b1", owner_id=None) == 0


def test_a_write_failure_never_stops_the_bot():
    c = Client(rows=[trade()], upsert_boom=RuntimeError("postgrest down"))
    assert archive.sync(c, bot_instance_id="b1", owner_id=None) == 0


def test_nothing_closed_yet_writes_nothing():
    assert archive.sync(Client(rows=[]), bot_instance_id="b1", owner_id=None) == 0
