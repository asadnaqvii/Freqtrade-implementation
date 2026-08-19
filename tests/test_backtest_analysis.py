"""Tests for period returns and for judging whether a backtest means anything.

The verdict tests matter most. A backtest always produces a number, and the
number is often meaningless -- nine trades in a bull market can read +400%. If
this analysis blesses that, it is worse than not having it, because it puts a
confident label on noise.
"""

from __future__ import annotations

import pytest

from app.backtest import verdict
from app.backtest.periods import PERIODS, bucket, breakdown, summarise
from datetime import datetime, timezone


def trade(close, profit, stake=100.0, pair="BTC/USDT", duration=240):
    return {"close_date": close, "profit_abs": profit, "stake_amount": stake,
            "pair": pair, "trade_duration_min": duration}


# ---------------------------------------------------------------------------
# Period bucketing
# ---------------------------------------------------------------------------

def test_every_period_produces_a_stable_key_and_a_readable_label():
    moment = datetime(2024, 5, 14, 9, 30, tzinfo=timezone.utc)
    assert bucket(moment, "day") == ("2024-05-14", "2024-05-14")
    assert bucket(moment, "month")[1] == "May 2024"
    assert bucket(moment, "quarter") == ("2024-Q2", "Q2 2024")
    assert bucket(moment, "year") == ("2024", "2024")
    key, label = bucket(moment, "week")
    assert key.startswith("2024-W") and "week" in label


def test_an_unknown_period_is_refused():
    with pytest.raises(ValueError):
        breakdown([], period="fortnight")


def test_keys_sort_chronologically_across_a_year_boundary():
    # Naive string sorting of "2024-W9" would place it after "2024-W10".
    late = bucket(datetime(2024, 12, 30, tzinfo=timezone.utc), "week")[0]
    early = bucket(datetime(2024, 3, 1, tzinfo=timezone.utc), "week")[0]
    assert early < late


# ---------------------------------------------------------------------------
# The two return figures
# ---------------------------------------------------------------------------

def test_account_return_compounds_and_capital_return_does_not():
    rows = breakdown(
        [trade("2024-01-05", 50, stake=100), trade("2024-02-05", 50, stake=100)],
        period="month", starting_balance=1000,
    )
    assert rows[0]["profit_pct"] == pytest.approx(5.0)
    # Second month starts from 1050, so the same 50 is a smaller share.
    assert rows[1]["profit_pct"] == pytest.approx(50 / 1050 * 100, abs=1e-3)
    # Return on capital is per-period and does not compound.
    assert rows[0]["return_on_capital_pct"] == pytest.approx(50.0)
    assert rows[1]["return_on_capital_pct"] == pytest.approx(50.0)


def test_return_on_capital_is_profit_over_what_was_actually_invested():
    rows = breakdown([trade("2024-01-05", 30, stake=200)], period="month",
                     starting_balance=10_000)
    assert rows[0]["staked"] == 200
    assert rows[0]["return_on_capital_pct"] == pytest.approx(15.0)
    # The account barely moved; the deployed capital worked hard. Both are true.
    assert rows[0]["profit_pct"] == pytest.approx(0.3)


def test_open_trades_are_not_counted_as_returns():
    rows = breakdown(
        [trade("2024-01-05", 50), {"close_date": None, "profit_abs": 999, "stake_amount": 10}],
        period="month", starting_balance=1000,
    )
    assert len(rows) == 1 and rows[0]["profit_abs"] == 50


def test_percentages_are_omitted_rather_than_faked_without_a_balance():
    rows = breakdown([trade("2024-01-05", 50)], period="month", starting_balance=None)
    assert rows[0]["profit_pct"] is None
    assert rows[0]["return_on_capital_pct"] is not None


# ---------------------------------------------------------------------------
# Consistency summary
# ---------------------------------------------------------------------------

def test_summary_finds_the_longest_losing_run():
    rows = breakdown([
        trade("2024-01-05", 10), trade("2024-02-05", -10), trade("2024-03-05", -10),
        trade("2024-04-05", -10), trade("2024-05-05", 40),
    ], period="month", starting_balance=1000)
    s = summarise(rows)
    assert s["periods"] == 5 and s["up"] == 2 and s["down"] == 3
    assert s["longest_losing_streak"] == 3
    assert s["worst"]["label"] in ("Feb 2024", "Mar 2024", "Apr 2024")


def test_summary_of_nothing_does_not_explode():
    assert summarise([])["periods"] == 0


# ---------------------------------------------------------------------------
# Whether the result is believable
# ---------------------------------------------------------------------------

def find(assessment, code):
    return next((f for f in assessment.findings if f.code == code), None)


def test_a_tiny_sample_is_called_out_as_meaningless():
    a = verdict.assess({"total_trades": 9, "wins": 8, "profit_total_pct": 400})
    f = find(a, "sample.trades")
    assert f.verdict == verdict.BAD
    assert a.headline == "Do not act on this result"


def test_a_large_sample_passes():
    a = verdict.assess({"total_trades": 400, "wins": 220, "profit_total_pct": 30})
    assert find(a, "sample.trades").verdict == verdict.GOOD


def test_losing_to_buy_and_hold_is_a_hard_finding():
    a = verdict.assess({
        "total_trades": 300, "wins": 200, "profit_total_pct": 40,
        "raw_metrics": {"market_change": 1.8},   # market rose 180%
    })
    f = find(a, "market.buy_and_hold")
    assert f.verdict == verdict.BAD
    assert "holding" in f.message.lower()


def test_making_money_in_a_falling_market_is_recognised():
    a = verdict.assess({
        "total_trades": 300, "wins": 180, "profit_total_pct": 25,
        "raw_metrics": {"market_change": -0.4},
    })
    assert find(a, "market.buy_and_hold").verdict == verdict.GOOD


def test_a_drawdown_deeper_than_the_profit_is_flagged():
    a = verdict.assess({"total_trades": 200, "wins": 120,
                        "profit_total_pct": 12, "max_drawdown_pct": 35})
    f = find(a, "risk.drawdown")
    assert f.verdict == verdict.BAD


def test_one_trade_carrying_the_whole_result_is_flagged():
    trades = [trade(f"2024-01-{d:02d}", 1) for d in range(1, 21)] + \
             [trade("2024-02-01", 500)]
    a = verdict.assess({"total_trades": 21, "wins": 21, "profit_total_abs": 520}, trades)
    f = find(a, "concentration.trade")
    assert f.verdict == verdict.BAD
    assert "96%" in f.message or "9" in f.message


def test_one_pair_carrying_the_result_is_flagged():
    trades = [trade("2024-01-05", 100, pair="BTC/USDT"),
              trade("2024-01-06", 5, pair="ETH/USDT"),
              trade("2024-01-07", 5, pair="SOL/USDT")]
    a = verdict.assess({"total_trades": 3, "wins": 3, "profit_total_abs": 110,
                        "pairs": ["BTC/USDT", "ETH/USDT", "SOL/USDT"]}, trades)
    assert find(a, "concentration.pair").verdict == verdict.BAD


def test_an_implausible_win_rate_on_a_small_sample_is_flagged():
    a = verdict.assess({"total_trades": 40, "wins": 38, "profit_total_pct": 60})
    assert find(a, "overfit.win_rate").verdict == verdict.BAD


def test_a_short_window_is_not_a_test():
    a = verdict.assess({
        "total_trades": 200, "wins": 120,
        "timerange_start": "2024-01-01T00:00:00Z", "timerange_end": "2024-02-01T00:00:00Z",
    })
    assert find(a, "window.length").verdict == verdict.BAD


def test_a_multi_year_window_passes():
    a = verdict.assess({
        "total_trades": 200, "wins": 120,
        "timerange_start": "2020-01-01T00:00:00Z", "timerange_end": "2024-01-01T00:00:00Z",
    })
    assert find(a, "window.length").verdict == verdict.GOOD


def test_exits_inside_one_candle_are_flagged_as_optimistic():
    trades = [trade("2024-01-05", 5, duration=30)]
    a = verdict.assess({"total_trades": 1, "wins": 1, "timeframe": "4h",
                        "avg_trade_duration_min": 30}, trades)
    f = find(a, "costs.duration")
    assert f.verdict == verdict.WEAK
    assert "candle" in f.message.lower() or "candle" in (f.detail or "").lower()


def test_no_trades_at_all_says_so_plainly():
    a = verdict.assess({"total_trades": 0, "wins": 0})
    assert find(a, "sample.trades").verdict == verdict.BAD
    assert "no trades" in find(a, "sample.trades").message.lower()


def test_a_clean_run_gets_a_clean_headline():
    a = verdict.assess({
        "total_trades": 480, "wins": 250, "profit_total_pct": 90,
        "profit_total_abs": 900, "max_drawdown_pct": 18, "sharpe": 1.6,
        "timerange_start": "2021-01-01T00:00:00Z", "timerange_end": "2024-06-01T00:00:00Z",
        "pairs": ["BTC/USDT", "ETH/USDT", "SOL/USDT"], "timeframe": "4h",
        "raw_metrics": {"market_change": 0.2},
    }, [trade(f"2024-01-{d:02d}", 12, pair=p)
        for d in range(1, 26) for p in ("BTC/USDT", "ETH/USDT", "SOL/USDT")])
    assert a.counts[verdict.BAD] == 0, [f.code for f in a.findings if f.verdict == verdict.BAD]
    assert a.headline in ("This test could actually tell you something",
                          "Reasonable, with caveats")


def test_nan_metrics_do_not_crash_the_assessment():
    a = verdict.assess({"total_trades": 50, "wins": 25, "sharpe": float("nan"),
                        "profit_total_pct": float("inf"), "max_drawdown_pct": None})
    assert a.findings


def test_a_losing_run_is_described_plainly_not_as_a_ratio():
    # "lost 2.1% from a peak to make -2.1%" reads like a bug. A negative return
    # has no drawdown trade-off to weigh.
    a = verdict.assess({"total_trades": 101, "wins": 24,
                        "profit_total_pct": -2.1, "max_drawdown_pct": 2.1})
    f = find(a, "risk.drawdown")
    assert f.verdict == verdict.BAD
    assert "lost money overall" in f.message
    assert "to make -" not in f.message
