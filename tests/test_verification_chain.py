"""Lining the three records up against each other.

    the strategy said  ->  the bot instructed  ->  the exchange filled

The first arrow was never checked. A signal the bot could not act on leaves no
order, so an order log cannot show it, and "the strategy is quiet" and "the
strategy is shouting and the bot cannot move" looked identical.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.api.routers import verification as v

NOW = datetime.now(timezone.utc)


def iso(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat()


class DB:
    """Stands in for the caller's RLS-scoped client."""

    def __init__(self, signals=None, orders=None, recon=None, missing=()):
        self.data = {
            "strategy_signals": signals or [],
            "v_live_orders": orders or [],
            "order_reconciliations": recon or [],
        }
        self.missing = set(missing)

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        if table in self.missing:
            raise RuntimeError(f"relation {table} does not exist")
        return list(self.data.get(table, []))


def signal(**kw):
    base = {"source": "bot", "strategy": "S", "pair": "BTC/USDT", "timeframe": "5m",
            "side": "enter_long", "bar_time": iso(60), "price": 100.0}
    return {**base, **kw}


def order(**kw):
    base = {"ft_order_id": "1", "ft_trade_id": 1, "pair": "BTC/USDT",
            "exchange_order_id": "x1", "status": "closed", "side": "buy",
            "price": 100.0, "average": 100.0, "amount": 1.0, "filled": 1.0,
            "order_date": iso(58)}
    return {**base, **kw}


def run(db, **kw):
    return asyncio.run(v.chain(db, **kw))


# ---------------------------------------------------------------------------
# The gap that had nowhere to appear
# ---------------------------------------------------------------------------

def test_a_signal_the_bot_never_acted_on_is_reported():
    out = run(DB(signals=[signal()], orders=[]))
    assert [r["outcome"] for r in out["rows"]] == ["signal_not_acted"]
    assert out["counts"]["signal_not_acted"] == 1


def test_a_signal_acted_on_and_confirmed_reads_as_confirmed():
    out = run(DB(signals=[signal()], orders=[order()],
                 recon=[{"exchange_order_id": "x1", "matched": True,
                         "discrepancy_kind": None, "notes": "agree",
                         "checked_at": iso(1)}]))
    assert out["rows"][0]["outcome"] == "confirmed"


def test_an_order_the_exchange_disputes_is_not_confirmed():
    out = run(DB(signals=[signal()], orders=[order()],
                 recon=[{"exchange_order_id": "x1", "matched": False,
                         "discrepancy_kind": "amount", "notes": "partial fill",
                         "checked_at": iso(1)}]))
    assert out["rows"][0]["outcome"] == "exchange_disagrees"


def test_an_order_not_yet_reconciled_says_so_rather_than_claiming_confirmed():
    out = run(DB(signals=[signal()], orders=[order()]))
    assert out["rows"][0]["outcome"] == "acted_unverified", (
        "silence from the exchange check is not agreement"
    )


def test_an_order_with_no_signal_behind_it_is_surfaced():
    """A force-exit, a stoploss the dataframe does not carry, or a trade nobody
    here can account for. All worth seeing."""
    out = run(DB(signals=[], orders=[order()]))
    assert out["rows"][0]["outcome"] == "order_without_signal"


# ---------------------------------------------------------------------------
# Matching a signal to the order it caused
# ---------------------------------------------------------------------------

def test_an_order_before_its_signal_is_not_matched_to_it():
    """Causality: freqtrade places after the bar closes, never before it."""
    out = run(DB(signals=[signal(bar_time=iso(30))],
                 orders=[order(order_date=iso(40))]))
    kinds = {r["outcome"] for r in out["rows"]}
    assert kinds == {"signal_not_acted", "order_without_signal"}


def test_an_order_far_after_its_signal_is_not_matched_to_it():
    # 5m bars, three-bar window: 40 minutes later is a different trade.
    out = run(DB(signals=[signal(bar_time=iso(60))],
                 orders=[order(order_date=iso(20))]))
    assert {r["outcome"] for r in out["rows"]} == {"signal_not_acted",
                                                   "order_without_signal"}


def test_the_window_scales_with_the_timeframe():
    """Twenty minutes after a 5m signal is late; after a 4h signal it is prompt."""
    late = order(order_date=iso(40))

    # Rows sort newest-first, and an unmatched order carries its own timestamp,
    # so compare the set rather than a position.
    on_5m = {r["outcome"] for r in run(DB(
        signals=[signal(timeframe="5m", bar_time=iso(60))], orders=[late]))["rows"]}
    assert on_5m == {"signal_not_acted", "order_without_signal"}

    on_4h = {r["outcome"] for r in run(DB(
        signals=[signal(timeframe="4h", bar_time=iso(60))], orders=[late]))["rows"]}
    assert on_4h == {"acted_unverified"}


def test_a_sell_order_does_not_satisfy_an_entry_signal():
    out = run(DB(signals=[signal(side="enter_long")],
                 orders=[order(side="sell")]))
    assert {r["outcome"] for r in out["rows"]} == {"signal_not_acted",
                                                   "order_without_signal"}


def test_another_pairs_order_does_not_satisfy_this_signal():
    out = run(DB(signals=[signal(pair="BTC/USDT")],
                 orders=[order(pair="ETH/USDT")]))
    assert {r["outcome"] for r in out["rows"]} == {"signal_not_acted",
                                                   "order_without_signal"}


def test_one_order_cannot_satisfy_two_signals():
    """Otherwise a single fill makes every repeated signal look acted upon."""
    out = run(DB(signals=[signal(bar_time=iso(60)), signal(bar_time=iso(59))],
                 orders=[order(order_date=iso(58))]))
    outcomes = sorted(r["outcome"] for r in out["rows"])
    assert outcomes == ["acted_unverified", "signal_not_acted"]


# ---------------------------------------------------------------------------
# Sources other than the running bot
# ---------------------------------------------------------------------------

def test_a_tradingview_signal_is_not_treated_as_the_bots_own():
    """It is a second opinion, not the record of what this strategy computed.

    Counting it here would let an independent implementation's disagreement read
    as the bot having missed a trade.
    """
    out = run(DB(signals=[signal(source="tradingview")], orders=[]))
    assert out["rows"] == []
    assert out["signals_recorded"] == 1


# ---------------------------------------------------------------------------
# Degrading honestly
# ---------------------------------------------------------------------------

def test_a_bot_that_has_never_written_signals_says_so():
    out = run(DB(missing=["strategy_signals"], orders=[order()]))
    assert "give it a few minutes" in out["note"]
    assert out["rows"][0]["outcome"] == "order_without_signal"


def test_missing_tables_do_not_500_the_page():
    out = run(DB(missing=["strategy_signals", "v_live_orders",
                          "order_reconciliations"]))
    assert out["rows"] == [] and out["counts"] == {}


def test_the_window_is_bounded():
    for bad in (0, -1, 91, 1000):
        with pytest.raises(Exception):
            run(DB(), days=bad)
