"""Comparing one strategy's run against another's.

The dashboard could say which strategy ran when, and how much it made. That
cannot answer the question it exists to answer -- "is the new one better" --
for two reasons. A run of five days and a run of three weeks produce totals
that are not comparable, and a strategy can be up over a sample while losing
money per unit of risk.

The live record is the worked example. Over 20-25 August the bot showed an 81%
win rate, which reads as excellent; its average loss was more than double its
average win, and only the trade count kept it positive. Win rate alone hides
exactly that, so these tests pin the numbers that do not.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.api.routers.bots import _days_between, _performance


def trade(profit, pair="BTC/USDT", reason="roi"):
    return {"close_profit_abs": profit, "pair": pair, "exit_reason": reason}


def test_a_high_win_rate_can_still_be_a_losing_system():
    """Four wins, one loss, 80% -- and negative expectancy. This is the shape
    the backtests showed for v1: winners clipped by the trailing stop, losers
    run to the -6% stop."""
    perf = _performance([trade(1.5), trade(1.2), trade(1.8), trade(1.1),
                         trade(-6.0, "ZEC/USDT", "stop_loss")])
    assert perf["win_rate"] == 80.0
    assert perf["profit_abs"] < 0
    assert perf["expectancy"] < 0
    assert perf["profit_factor"] < 1


def test_profit_factor_is_gross_win_over_gross_loss():
    perf = _performance([trade(10.0), trade(10.0), trade(-5.0)])
    assert perf["profit_factor"] == 4.0
    assert perf["avg_win"] == 10.0
    assert perf["avg_loss"] == -5.0


def test_profit_factor_is_undefined_rather_than_infinite():
    """"inf" on a five-trade sample is not a useful headline, and a dashboard
    that prints it invites exactly the wrong conclusion."""
    assert _performance([trade(1.0), trade(2.0)])["profit_factor"] is None
    assert _performance([trade(-1.0)])["profit_factor"] is None


def test_drawdown_is_the_worst_peak_to_trough_not_the_worst_trade():
    """Three consecutive losses hurt more than the largest single one, and it
    is the run of them that empties an account."""
    # Newest-first, as every trade list in this module is.
    run = [trade(-2.0), trade(-3.0), trade(-4.0), trade(10.0)]
    perf = _performance(run)
    assert perf["max_drawdown"] == -9.0
    assert perf["profit_abs"] == 1.0


def test_drawdown_is_zero_when_nothing_ever_gave_back():
    assert _performance([trade(1.0), trade(2.0)])["max_drawdown"] == 0.0


def test_exit_reasons_show_the_mechanism_not_just_the_result():
    """A run that is up because ROI fired and one that is up because nothing
    has hit its stop yet are not the same run."""
    perf = _performance([trade(1.0), trade(1.0, reason="trailing_stop_loss"),
                         trade(-2.0, reason="stop_loss"), trade(1.0)])
    assert perf["exits"] == {"roi": 2, "trailing_stop_loss": 1, "stop_loss": 1}
    # Most frequent first, so the headline reason is the one you read.
    assert list(perf["exits"])[0] == "roi"


def test_the_worst_pair_is_reported_not_only_the_best():
    """The best pair is nice to know; the worst is the one you act on."""
    perf = _performance([trade(5.0, "BTC/USDT"), trade(-8.0, "ZEC/USDT"),
                         trade(2.0, "BTC/USDT")])
    assert perf["best"] == {"name": "BTC/USDT", "profit_abs": 7.0}
    assert perf["worst"] == {"name": "ZEC/USDT", "profit_abs": -8.0}


def test_an_empty_run_reports_nothing_rather_than_zero():
    """A strategy that has not traded has no win rate. Reporting 0% would read
    as "it lost every trade"."""
    perf = _performance([])
    assert perf["trades"] == 0
    assert perf["win_rate"] is None
    assert perf["expectancy"] is None
    assert perf["profit_factor"] is None


def test_trades_with_no_recorded_profit_are_not_counted():
    """Open positions come through the same list and have no result yet."""
    perf = _performance([trade(1.0), {"close_profit_abs": None, "pair": "X/USDT"}])
    assert perf["trades"] == 1


# ---------------------------------------------------------------------------
# Normalising for length, which is what makes two runs comparable at all
# ---------------------------------------------------------------------------

def test_a_finished_run_is_measured_between_its_own_dates():
    days = _days_between("2026-08-02T14:49:28+00:00", "2026-08-19T22:57:15+00:00")
    assert 17.3 < days < 17.4


def test_a_running_deployment_is_measured_up_to_now():
    started = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert 2.9 < _days_between(started, None) < 3.1


def test_a_naive_timestamp_is_read_as_utc():
    """These columns are `timestamp without time zone` holding UTC. Reading one
    as local time would shift a deployment window by hours and move trades
    across the boundary between two strategies."""
    naive = _days_between("2026-08-02T00:00:00", "2026-08-12T00:00:00")
    aware = _days_between("2026-08-02T00:00:00+00:00", "2026-08-12T00:00:00+00:00")
    assert naive == aware == 10.0


def test_an_unparseable_or_backwards_window_is_reported_as_unknown():
    """Better a blank cell than a per-day figure divided by a nonsense span."""
    assert _days_between(None, None) is None
    assert _days_between("not a date", None) is None
    assert _days_between("2026-08-12T00:00:00Z", "2026-08-02T00:00:00Z") is None
