"""Is this backtest result worth believing?

A backtest always produces a number. The number is frequently meaningless, and
nothing in the output says so -- freqtrade will happily report +400% from nine
trades in a bull market, and it looks exactly like a real edge.

So this reads a finished run and says what is wrong with it. Every finding names
a specific failure: not "low sample size" but "23 trades cannot distinguish a 55%
win rate from a coin flip". The point is to make an untrustworthy result *look*
untrustworthy next to a trustworthy one.

None of this says a strategy will make money. It says whether the test was
capable of telling you.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

GOOD, WEAK, BAD = "good", "weak", "bad"

#: Below this, a win rate is barely distinguishable from chance. The 95%
#: confidence interval on 30 coin flips still spans roughly 32%-68%.
MIN_TRADES_MEANINGFUL = 30
#: Where the interval narrows enough to act on.
MIN_TRADES_CONFIDENT = 100


@dataclass
class Finding:
    code: str
    title: str
    verdict: str
    message: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code, "title": self.title, "verdict": self.verdict,
            "message": self.message, "detail": self.detail,
        }


@dataclass
class Assessment:
    findings: list[Finding] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return {
            level: sum(1 for f in self.findings if f.verdict == level)
            for level in (GOOD, WEAK, BAD)
        }

    @property
    def headline(self) -> str:
        counts = self.counts
        if counts[BAD]:
            return "Do not act on this result"
        if counts[WEAK] >= 3:
            return "Treat this as a rough hint, not evidence"
        if counts[WEAK]:
            return "Reasonable, with caveats"
        return "This test could actually tell you something"

    def as_dict(self) -> dict[str, Any]:
        return {
            "headline": self.headline,
            "counts": self.counts,
            "findings": [f.as_dict() for f in self.findings],
        }


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(result) or math.isinf(result) else result


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _wilson_halfwidth(wins: int, total: int) -> float | None:
    """How wide the 95% interval on this win rate is, in percentage points.

    Wilson rather than the textbook normal interval, which misbehaves badly at
    the small samples this is most needed for.
    """
    if total <= 0:
        return None
    z = 1.96
    p = wins / total
    denominator = 1 + z * z / total
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return margin * 100


def assess(run: dict[str, Any], trades: Sequence[dict[str, Any]] | None = None) -> Assessment:
    """Judge one finished run. `trades` is optional but sharpens several checks."""
    trades = list(trades or [])
    out: list[Finding] = []

    total = int(run.get("total_trades") or 0)
    wins = int(run.get("wins") or 0)
    profit_pct = _f(run.get("profit_total_pct"))
    profit_abs = _f(run.get("profit_total_abs"))
    pairs = list(run.get("pairs") or [])

    out.append(_check_sample(total, wins))
    out.append(_check_window(run, total))
    if pairs:
        out.append(_check_pair_coverage(total, pairs))
    out.append(_check_drawdown(run, profit_pct))
    out.append(_check_risk_adjusted(run))
    out.append(_check_versus_market(run, profit_pct))
    out.append(_check_win_rate_plausibility(run, total, wins))

    if trades:
        out.append(_check_concentration(trades, profit_abs))
        out.append(_check_pair_concentration(trades, profit_abs))
        out.append(_check_duration(run, trades))

    return Assessment([f for f in out if f is not None])


# ---------------------------------------------------------------------------
# The checks
# ---------------------------------------------------------------------------

def _check_sample(total: int, wins: int) -> Finding:
    margin = _wilson_halfwidth(wins, total)
    rate = (wins / total * 100) if total else 0.0

    if total == 0:
        return Finding(
            "sample.trades", "Sample size", BAD,
            "No trades were taken, so there is nothing to judge.",
            "The strategy never triggered over this window. Widen the timerange, "
            "add pairs, or loosen the entry conditions.",
        )
    if total < MIN_TRADES_MEANINGFUL:
        return Finding(
            "sample.trades", "Sample size", BAD,
            f"{total} trades is too few to mean anything. The true win rate could "
            f"plausibly be anywhere from {max(0, rate - (margin or 0)):.0f}% to "
            f"{min(100, rate + (margin or 0)):.0f}%.",
            f"Aim for at least {MIN_TRADES_MEANINGFUL} trades to see a signal and "
            f"{MIN_TRADES_CONFIDENT} to trust it. Longer window, more pairs, or a "
            "shorter timeframe.",
        )
    if total < MIN_TRADES_CONFIDENT:
        return Finding(
            "sample.trades", "Sample size", WEAK,
            f"{total} trades gives a rough picture: the win rate is {rate:.0f}% "
            f"give or take {margin:.0f} points.",
            f"Around {MIN_TRADES_CONFIDENT} trades is where that interval gets "
            "narrow enough to act on.",
        )
    return Finding(
        "sample.trades", "Sample size", GOOD,
        f"{total} trades, win rate {rate:.0f}% ± {margin:.0f} points.",
    )


def _check_window(run: dict[str, Any], total: int) -> Finding | None:
    start, end = _when(run.get("timerange_start")), _when(run.get("timerange_end"))
    if not start or not end:
        return None
    days = (end - start).days
    if days <= 0:
        return None

    if days < 90:
        return Finding(
            "window.length", "Test window", BAD,
            f"{days} days is one market mood, not a test.",
            "A strategy that only ever saw one trend has not been asked the "
            "question that matters. Use at least a year, ideally covering both a "
            "rise and a fall.",
        )
    if days < 365:
        return Finding(
            "window.length", "Test window", WEAK,
            f"{days} days covers less than a full year.",
            "Crypto regimes turn over in months. A year or more will include at "
            "least one reversal.",
        )
    years = days / 365.25
    return Finding(
        "window.length", "Test window", GOOD,
        f"{days} days ({years:.1f} years) — long enough to contain more than one regime.",
    )


def _check_pair_coverage(total: int, pairs: list[str]) -> Finding | None:
    per_pair = total / len(pairs) if pairs else 0
    if len(pairs) == 1:
        return Finding(
            "sample.pairs", "Pair coverage", WEAK,
            "Tested on a single pair, so the result may be that pair's history "
            "rather than the strategy's edge.",
            "Run the same strategy over several unrelated pairs. An edge that "
            "only exists on one is usually a coincidence.",
        )
    if per_pair < 10:
        return Finding(
            "sample.pairs", "Pair coverage", WEAK,
            f"About {per_pair:.0f} trades per pair across {len(pairs)} pairs — "
            "too thin to tell which pairs the strategy actually works on.",
        )
    return Finding(
        "sample.pairs", "Pair coverage", GOOD,
        f"{len(pairs)} pairs, roughly {per_pair:.0f} trades each.",
    )


def _check_drawdown(run: dict[str, Any], profit_pct: float | None) -> Finding | None:
    drawdown = _f(run.get("max_drawdown_pct"))
    if drawdown is None:
        return None
    drawdown = abs(drawdown)
    # freqtrade reports this as a ratio on some versions and a percent on others.
    if drawdown <= 1:
        drawdown *= 100
    if drawdown == 0:
        return None

    if profit_pct is None:
        return Finding("risk.drawdown", "Worst drop", WEAK,
                       f"Deepest fall from a peak was {drawdown:.1f}%.")

    ratio = profit_pct / drawdown
    if ratio < 1:
        return Finding(
            "risk.drawdown", "Worst drop", BAD,
            f"It lost {drawdown:.1f}% from a peak to make {profit_pct:.1f}%.",
            "You would have had to sit through a bigger fall than the whole "
            "profit. Most people close the bot before that recovers.",
        )
    if ratio < 2:
        return Finding(
            "risk.drawdown", "Worst drop", WEAK,
            f"{profit_pct:.1f}% profit against a {drawdown:.1f}% worst fall.",
            "A ratio under 2 means the ride is rough relative to the reward.",
        )
    return Finding(
        "risk.drawdown", "Worst drop", GOOD,
        f"{profit_pct:.1f}% profit against a {drawdown:.1f}% worst fall "
        f"({ratio:.1f}× more gain than pain).",
    )


def _check_risk_adjusted(run: dict[str, Any]) -> Finding | None:
    sharpe, sortino = _f(run.get("sharpe")), _f(run.get("sortino"))
    if sharpe is None and sortino is None:
        return None
    parts = []
    if sharpe is not None:
        parts.append(f"Sharpe {sharpe:.2f}")
    if sortino is not None:
        parts.append(f"Sortino {sortino:.2f}")
    summary = ", ".join(parts)

    reference = sharpe if sharpe is not None else sortino
    if reference is None:
        return None
    if reference < 0:
        return Finding("risk.adjusted", "Return per unit of risk", BAD,
                       f"{summary} — negative, meaning the volatility bought you nothing.")
    if reference < 1:
        return Finding("risk.adjusted", "Return per unit of risk", WEAK,
                       f"{summary}. Under 1 is generally considered weak.",
                       "The returns are small relative to how much they swung around.")
    return Finding("risk.adjusted", "Return per unit of risk", GOOD, f"{summary}.")


def _check_versus_market(run: dict[str, Any], profit_pct: float | None) -> Finding | None:
    """Did the strategy beat simply holding the coins over the same window?

    The single most common way a backtest flatters itself: a strategy that is
    long most of the time in a rising market looks brilliant and has no edge at
    all. freqtrade records the market's own move, so the comparison is free.
    """
    raw = run.get("raw_metrics") or {}
    market = _f(raw.get("market_change"))
    if market is None or profit_pct is None:
        return None
    market_pct = market * 100 if abs(market) <= 5 else market

    if market_pct > 0 and profit_pct < market_pct:
        return Finding(
            "market.buy_and_hold", "Versus just holding", BAD,
            f"The market rose {market_pct:.1f}% and the strategy made "
            f"{profit_pct:.1f}%. Buying and holding beat it.",
            "All the trading added was risk and fees. Whatever it is detecting, "
            "it is not worth the activity over this window.",
        )
    if market_pct > 0 and profit_pct < market_pct * 1.25:
        return Finding(
            "market.buy_and_hold", "Versus just holding", WEAK,
            f"{profit_pct:.1f}% against a market that rose {market_pct:.1f}% — "
            "barely ahead of doing nothing.",
        )
    if market_pct < 0 and profit_pct > 0:
        return Finding(
            "market.buy_and_hold", "Versus just holding", GOOD,
            f"Made {profit_pct:.1f}% while the market fell {abs(market_pct):.1f}%. "
            "That is the interesting case.",
        )
    return Finding(
        "market.buy_and_hold", "Versus just holding", GOOD,
        f"{profit_pct:.1f}% against a market move of {market_pct:.1f}%.",
    )


def _check_win_rate_plausibility(run: dict[str, Any], total: int, wins: int) -> Finding | None:
    if not total:
        return None
    rate = wins / total * 100
    if rate >= 85 and total < MIN_TRADES_CONFIDENT:
        return Finding(
            "overfit.win_rate", "Win rate plausibility", BAD,
            f"{rate:.0f}% winners over only {total} trades.",
            "Win rates that high on small samples almost always come from a "
            "strategy fitted to this exact history. Test it on a different "
            "window and different pairs before believing it.",
        )
    if rate >= 90:
        return Finding(
            "overfit.win_rate", "Win rate plausibility", WEAK,
            f"{rate:.0f}% winners is unusually high.",
            "Check the exit rules: a distant stop-loss with a near take-profit "
            "produces many small wins and rare huge losses. Look at the worst trade.",
        )
    return None


def _check_concentration(trades: Sequence[dict], profit_abs: float | None) -> Finding | None:
    profits = sorted((_f(t.get("profit_abs")) or 0.0) for t in trades)
    if not profits or not profit_abs or profit_abs <= 0:
        return None
    best = profits[-1]
    share = best / profit_abs * 100
    if share >= 50:
        return Finding(
            "concentration.trade", "Where the profit came from", BAD,
            f"One trade produced {share:.0f}% of all the profit.",
            "Remove that trade and the strategy is roughly flat. This is a lucky "
            "outlier, not a repeatable edge.",
        )
    if share >= 30:
        return Finding(
            "concentration.trade", "Where the profit came from", WEAK,
            f"The single best trade is {share:.0f}% of total profit.",
            "Worth checking that trade individually before trusting the total.",
        )
    return Finding(
        "concentration.trade", "Where the profit came from", GOOD,
        f"Profit is spread across trades; the best one is {share:.0f}% of the total.",
    )


def _check_pair_concentration(trades: Sequence[dict], profit_abs: float | None) -> Finding | None:
    if not profit_abs or profit_abs <= 0:
        return None
    by_pair: dict[str, float] = {}
    for trade in trades:
        by_pair[trade.get("pair") or "?"] = (
            by_pair.get(trade.get("pair") or "?", 0.0) + (_f(trade.get("profit_abs")) or 0.0)
        )
    if len(by_pair) < 2:
        return None
    top_pair, top_profit = max(by_pair.items(), key=lambda kv: kv[1])
    share = top_profit / profit_abs * 100
    if share >= 70:
        return Finding(
            "concentration.pair", "Spread across pairs", BAD,
            f"{top_pair} produced {share:.0f}% of the profit; the other "
            f"{len(by_pair) - 1} pairs contributed little.",
            "This looks like one pair's history rather than a general edge.",
        )
    if share >= 50:
        return Finding(
            "concentration.pair", "Spread across pairs", WEAK,
            f"{top_pair} accounts for {share:.0f}% of the profit.",
        )
    losing = sum(1 for value in by_pair.values() if value < 0)
    return Finding(
        "concentration.pair", "Spread across pairs", GOOD,
        f"Profit comes from several pairs; {len(by_pair) - losing} of "
        f"{len(by_pair)} were profitable.",
    )


def _check_duration(run: dict[str, Any], trades: Sequence[dict]) -> Finding | None:
    minutes = _f(run.get("avg_trade_duration_min"))
    if minutes is None:
        durations = [_f(t.get("trade_duration_min")) for t in trades]
        durations = [d for d in durations if d is not None]
        if not durations:
            return None
        minutes = sum(durations) / len(durations)

    candle = _timeframe_minutes(run.get("timeframe") or "")
    if candle and minutes < candle:
        return Finding(
            "costs.duration", "Trade length", WEAK,
            f"Average trade lasts {minutes:.0f} minutes on a "
            f"{run.get('timeframe')} candle.",
            "Exits inside a single candle are resolved from that candle's high "
            "and low, which a live bot cannot see in advance. Real results will "
            "be worse than this.",
        )
    return None


def _timeframe_minutes(timeframe: str) -> int | None:
    units = {"m": 1, "h": 60, "d": 1440, "w": 10080, "M": 43200}
    if not timeframe or timeframe[-1] not in units:
        return None
    try:
        return int(timeframe[:-1]) * units[timeframe[-1]]
    except ValueError:
        return None
