"""Group a backtest's trades into calendar periods.

"It made 34%" is not a useful sentence about a strategy. Whether that was a
steady climb or one enormous month carrying two years of bleeding is the whole
question, and only a period breakdown answers it.

Two return figures, because "return %" has two honest denominators and they
answer different questions:

  return on account   profit / the balance at the start of the period. What the
                      portfolio did. Compounds: a month turning 1000 into 1100
                      is +10%, and the next month starts from 1100.

  return on capital   profit / the money actually placed into trades that
                      period. How hard each deployed dollar worked. A strategy
                      that only ever commits 10% of the account can show a dull
                      account return and an excellent return on capital -- and
                      the gap between the two numbers is exactly the amount of
                      idle money, which is worth seeing.

Neither is more correct. Shown side by side, they say different true things.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

PERIODS = ("day", "week", "month", "quarter", "year")


def _f(value: Any) -> float:
    if value is None:
        return 0.0
    try:
        result = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if math.isnan(result) or math.isinf(result) else result


def _when(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def bucket(moment: datetime, period: str) -> tuple[str, str]:
    """(sort key, human label) for the period this moment falls in."""
    if period == "day":
        key = moment.strftime("%Y-%m-%d")
        return key, key
    if period == "week":
        # ISO weeks, so a week is Monday-Sunday and never splits a year oddly.
        year, week, _ = moment.isocalendar()
        return f"{year}-W{week:02d}", f"{year} week {week:02d}"
    if period == "month":
        return moment.strftime("%Y-%m"), moment.strftime("%b %Y")
    if period == "quarter":
        quarter = (moment.month - 1) // 3 + 1
        return f"{moment.year}-Q{quarter}", f"Q{quarter} {moment.year}"
    if period == "year":
        return str(moment.year), str(moment.year)
    raise ValueError(f"unknown period {period!r}; expected one of {', '.join(PERIODS)}")


def breakdown(
    trades: Iterable[dict[str, Any]],
    *,
    period: str = "month",
    starting_balance: float | None = None,
) -> list[dict[str, Any]]:
    """Per-period return, oldest first.

    A trade counts in the period it *closed* in, because that is when the money
    actually moved. Still-open trades have no realised result and are skipped.
    """
    if period not in PERIODS:
        raise ValueError(f"unknown period {period!r}; expected one of {', '.join(PERIODS)}")

    grouped: dict[str, dict[str, Any]] = {}
    for trade in trades:
        closed = _when(trade.get("close_date"))
        if closed is None:
            continue
        key, label = bucket(closed, period)
        row = grouped.setdefault(key, {
            "key": key, "label": label, "trades": 0, "wins": 0, "losses": 0,
            "profit_abs": 0.0, "staked": 0.0, "best": None, "worst": None,
            "pairs": set(),
        })
        profit = _f(trade.get("profit_abs"))
        row["trades"] += 1
        row["profit_abs"] += profit
        row["staked"] += _f(trade.get("stake_amount"))
        if profit > 0:
            row["wins"] += 1
        elif profit < 0:
            row["losses"] += 1
        row["best"] = profit if row["best"] is None else max(row["best"], profit)
        row["worst"] = profit if row["worst"] is None else min(row["worst"], profit)
        if trade.get("pair"):
            row["pairs"].add(trade["pair"])

    balance = starting_balance if starting_balance and starting_balance > 0 else None
    out: list[dict[str, Any]] = []
    for key in sorted(grouped):
        row = grouped[key]
        opening = balance
        profit = row["profit_abs"]
        # Percent is against the balance at the START of the period: that is the
        # capital that was actually at work, and it is what makes one period
        # comparable with another.
        pct = (profit / opening * 100) if opening else None
        staked = row["staked"]
        # Return on the capital actually committed, which is what "profit over
        # investment" means when the account is only partly deployed.
        on_capital = (profit / staked * 100) if staked else None
        if balance is not None:
            balance += profit
        out.append({
            "key": row["key"],
            "label": row["label"],
            "trades": row["trades"],
            "wins": row["wins"],
            "losses": row["losses"],
            "win_rate": (row["wins"] / row["trades"] * 100) if row["trades"] else None,
            "profit_abs": round(profit, 8),
            "profit_pct": round(pct, 4) if pct is not None else None,
            "staked": round(staked, 8),
            "return_on_capital_pct": round(on_capital, 4) if on_capital is not None else None,
            "opening_balance": round(opening, 8) if opening is not None else None,
            "closing_balance": round(balance, 8) if balance is not None else None,
            "best_trade": round(row["best"], 8) if row["best"] is not None else None,
            "worst_trade": round(row["worst"], 8) if row["worst"] is not None else None,
            "pairs": len(row["pairs"]),
        })
    return out


def summarise(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Headline numbers about the run of periods themselves.

    The consistency question: how many periods were up, what the worst one cost,
    and the longest losing streak. A strategy with a good total and eight losing
    months in a row is one you would have switched off in month four.
    """
    if not rows:
        return {"periods": 0}

    up = [r for r in rows if (r["profit_abs"] or 0) > 0]
    down = [r for r in rows if (r["profit_abs"] or 0) < 0]

    longest_down = streak = 0
    for row in rows:
        if (row["profit_abs"] or 0) < 0:
            streak += 1
            longest_down = max(longest_down, streak)
        else:
            streak = 0

    total_profit = sum(r["profit_abs"] or 0 for r in rows)
    total_staked = sum(r.get("staked") or 0 for r in rows)

    best = max(rows, key=lambda r: r["profit_abs"] or 0)
    worst = min(rows, key=lambda r: r["profit_abs"] or 0)
    return {
        "periods": len(rows),
        "up": len(up),
        "down": len(down),
        "hit_rate": round(len(up) / len(rows) * 100, 1),
        "longest_losing_streak": longest_down,
        "total_profit_abs": round(total_profit, 8),
        "total_staked": round(total_staked, 8),
        "return_on_capital_pct": (
            round(total_profit / total_staked * 100, 4) if total_staked else None
        ),
        "best": {"label": best["label"], "profit_abs": best["profit_abs"],
                 "profit_pct": best["profit_pct"]},
        "worst": {"label": worst["label"], "profit_abs": worst["profit_abs"],
                  "profit_pct": worst["profit_pct"]},
    }


# ---------------------------------------------------------------------------
# Did the backtest actually cover what was asked for?
# ---------------------------------------------------------------------------

def parse_timerange(text: str | None) -> tuple[datetime | None, datetime | None]:
    """freqtrade's YYYYMMDD-YYYYMMDD, either side optionally blank."""
    if not text or "-" not in text:
        return None, None
    start_text, _, end_text = text.partition("-")

    def one(part: str) -> datetime | None:
        part = part.strip()
        if len(part) != 8 or not part.isdigit():
            return None
        try:
            return datetime.strptime(part, "%Y%m%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

    return one(start_text), one(end_text)


def coverage(requested: str | None, actual_start: Any, actual_end: Any,
             *, timeframe: str | None = None) -> dict[str, Any]:
    """Compare the window asked for with the window that ran.

    Exists because the gap used to be invisible: a request for ten years that
    found one month of candles produced a successful-looking run over that
    month. The number is the point, but so is the explanation -- the usual cause
    is the venue keeping far less history at fine timeframes than at coarse ones,
    and that is actionable in a way that "no data" is not.
    """
    want_start, want_end = parse_timerange(requested)
    got_start, got_end = _when(actual_start), _when(actual_end)
    if not want_start and not want_end:
        return {"requested_timerange": requested, "coverage_pct": None, "coverage_note": None}
    if not got_start or not got_end:
        return {
            "requested_timerange": requested, "coverage_pct": 0.0,
            "coverage_note": "The backtest produced no usable window at all.",
        }

    want_start = want_start or got_start
    want_end = want_end or got_end
    wanted_days = max((want_end - want_start).days, 0)
    got_days = max((got_end - got_start).days, 0)
    if wanted_days <= 0:
        return {"requested_timerange": requested, "coverage_pct": None, "coverage_note": None}

    pct = min(100.0, got_days / wanted_days * 100.0)
    if pct >= 95:
        return {"requested_timerange": requested, "coverage_pct": round(pct, 2),
                "coverage_note": None}

    note = (
        f"You asked for {wanted_days} days ({want_start:%Y-%m-%d} to {want_end:%Y-%m-%d}) "
        f"and only {got_days} days were available "
        f"({got_start:%Y-%m-%d} to {got_end:%Y-%m-%d}) — {pct:.0f}% of the request. "
    )
    note += (
        "That is where this exchange's candles for these pairs begin. Another venue "
        "may go further back — check with the history button on the backtest form, "
        "and remember the exchange you backtest on need not be the one you trade on."
    )
    return {"requested_timerange": requested, "coverage_pct": round(pct, 2),
            "coverage_note": note}
