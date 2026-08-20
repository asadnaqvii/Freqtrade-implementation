"""Deployed bots: registration, heartbeat and live trade history."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.backtest import periods
from app.api.deps import UserDB

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/bots", tags=["bots"])


@router.get("")
async def list_bots(db: UserDB) -> dict:
    # v_bot_health turns last_heartbeat_at into a verdict, so a bot that stopped
    # reporting does not keep showing whatever status it last wrote.
    return {"bots": db.select("v_bot_health", order="name.asc")}


# Literal paths must be declared before the parameterised ones they could be
# mistaken for. FastAPI matches routes in declaration order, so with
# /{bot_id}/trades first, a request for /live/trades bound bot_id="live" and the
# query went out as bot_instance_id=eq.live -- which Postgres rejects as a
# malformed uuid. The symptom was an empty Live bot tab, nowhere near the cause.
@router.get("/live/trades")
async def live_trades(db: UserDB, limit: int = 100) -> dict:
    """Read straight from the bot's own tables via the live view.

    Returns an empty list rather than an error when the view does not exist yet:
    it is created the first time the bot connects, so before then "no trades" is
    the honest answer, not a fault.
    """
    try:
        return {"trades": db.select("v_live_trades", order="open_date.desc", limit=min(limit, 500))}
    except Exception as exc:
        log.info("live trade view unavailable: %s", exc)
        return {"trades": [], "note": "the bot has not created its tables yet"}


#: PostgREST answers at most this many rows per request whatever `limit` says.
POSTGREST_PAGE = 1000


def _all_rows(db, table: str, *, columns: str, filters: dict, order: str,
              cap: int = 20000) -> list[dict]:
    """Every row, paged past PostgREST's per-response ceiling."""
    out: list[dict] = []
    while len(out) < cap:
        rows = db.select(table, columns=columns, filters=filters, order=order,
                         limit=POSTGREST_PAGE, offset=len(out))
        out.extend(rows)
        if len(rows) < POSTGREST_PAGE:
            break
    return out[:cap]


@router.get("/history")
async def trade_history(db: UserDB, limit: int = 20000) -> dict:
    """Every trade this account has, wherever it currently lives.

    Two stores, one answer. `trade_archive` holds what has been preserved across
    deployments -- including the Railway history, which no longer exists
    anywhere else. `v_live_trades` reads the running bot's own tables, which
    hold only what it has done since it last started. Neither alone is "your
    trades", and a panel that shows one of them looks like data loss when the
    other is where the history went.

    Declared before /{bot_id}/trades: a literal path after a parameterised one
    never gets reached.
    """
    rows = _merged_trades(db, limit)
    closed = [t for t in rows if not t.get("is_open")]
    profits = [float(t["close_profit_abs"]) for t in closed
               if t.get("close_profit_abs") is not None]
    wins = [p for p in profits if p > 0]
    return {
        "trades": rows,
        "counts": {
            "total": len(rows),
            "open": sum(1 for t in rows if t.get("is_open")),
            "closed": len(closed),
            "live": sum(1 for t in rows if t["source"] == "live"),
            "archive": sum(1 for t in rows if t["source"] == "archive"),
        },
        "totals": {
            "profit_abs": round(sum(profits), 8) if profits else 0.0,
            "wins": len(wins),
            "losses": len(profits) - len(wins),
            "win_rate": round(len(wins) / len(profits) * 100, 2) if profits else None,
            "first_open": rows[-1].get("open_date") if rows else None,
            "last_open": rows[0].get("open_date") if rows else None,
        },
        # Gross win over gross loss. Undefined rather than infinite when nothing
        # has lost yet: "inf" on a five-trade sample is not a useful headline.
        "profit_factor": (
            round(sum(wins) / abs(sum(p for p in profits if p < 0)), 4)
            if any(p < 0 for p in profits) and wins else None
        ),
        "expectancy": round(sum(profits) / len(profits), 8) if profits else None,
        "best": _best_pair(closed),
    }


def _best_pair(closed: list[dict]) -> dict:
    """The pair with the most realised profit, over the whole history."""
    totals: dict[str, float] = {}
    for t in closed:
        if t.get("close_profit_abs") is None or not t.get("pair"):
            continue
        totals[t["pair"]] = totals.get(t["pair"], 0.0) + float(t["close_profit_abs"])
    if not totals:
        return {}
    name = max(totals, key=lambda k: totals[k])
    return {"name": name, "profit_abs": round(totals[name], 8)}


def _merged_trades(db, limit: int = 20000) -> list[dict]:
    """Every trade this account has, newest first, from both stores."""
    rows: list[dict] = []
    seen: set[tuple] = set()

    def collect(table: str, source: str) -> None:
        try:
            found = _all_rows(db, table, columns="*", filters={},
                              order="open_date.desc", cap=limit)
        except Exception as exc:  # noqa: BLE001 - the view exists only after first connect
            log.info("%s unavailable: %s", table, exc)
            return
        for t in found:
            # Same trade, both stores: pair plus the open instant identifies it
            # across two schemas that do not share an id space.
            key = (t.get("pair"), str(t.get("open_date"))[:19])
            if key in seen:
                continue
            seen.add(key)
            rows.append({**t, "source": source})

    # Live first: where a trade is in both, the bot's copy is the fresher one.
    collect("v_live_trades", "live")
    collect("trade_archive", "archive")

    rows.sort(key=lambda t: str(t.get("open_date") or ""), reverse=True)
    return rows


@router.get("/history/breakdown")
async def history_breakdown(db: UserDB, period: str = "day", limit: int = 400) -> dict:
    """Realised profit per day / week / month, over the whole history.

    Not freqtrade's /daily: that reports only what the running bot has in its
    own tables, so after a redeploy or a dry-run-to-live cutover it draws a row
    of zeroes over an account that has been trading for weeks. This buckets the
    same merged set the trade table shows.

    Declared before /{bot_id}/trades, which would otherwise match `history` as
    a bot id.
    """
    if period not in periods.PERIODS:
        raise HTTPException(
            status_code=400,
            detail=f"unknown period {period!r}; expected one of {', '.join(periods.PERIODS)}",
        )

    # periods.breakdown() speaks the backtest column names; the live stores use
    # freqtrade's. Translate rather than teaching it two vocabularies.
    trades = [
        {
            "close_date": t.get("close_date"),
            "pair": t.get("pair"),
            "profit_abs": t.get("close_profit_abs"),
            "stake_amount": t.get("stake_amount"),
        }
        for t in _merged_trades(db)
        if not t.get("is_open") and t.get("close_date")
    ]
    rows = periods.breakdown(trades, period=period)
    return {"period": period, "rows": rows[-limit:], "total_periods": len(rows)}


#: What a performance view can group by, and where that value lives on a trade.
PERFORMANCE_KEYS = {"pair": "pair", "enter_tag": "enter_tag", "exit_reason": "exit_reason"}


@router.get("/history/performance")
async def history_performance(db: UserDB, by: str = "pair", limit: int = 100) -> dict:
    """Realised performance grouped by pair, entry tag or exit reason.

    Not freqtrade's /performance, /entries, /exits: those report only what the
    running bot holds in its own tables. After a redeploy -- or a dry-run to
    live cutover -- that is a fraction of the account's actual history, shown
    with nothing on screen to say so.

    Declared before /{bot_id}/trades, which would match `history` as a bot id.
    """
    field = PERFORMANCE_KEYS.get(by)
    if field is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown grouping {by!r}; expected one of {', '.join(PERFORMANCE_KEYS)}",
        )

    groups: dict[str, dict] = {}
    for t in _merged_trades(db):
        if t.get("is_open") or t.get("close_profit_abs") is None:
            continue
        name = t.get(field) or "—"
        row = groups.setdefault(str(name), {
            "name": str(name), "count": 0, "wins": 0, "losses": 0,
            "profit_abs": 0.0, "staked": 0.0,
        })
        profit = float(t["close_profit_abs"])
        row["count"] += 1
        row["profit_abs"] += profit
        row["staked"] += float(t.get("stake_amount") or 0)
        if profit > 0:
            row["wins"] += 1
        elif profit < 0:
            row["losses"] += 1

    rows = sorted(groups.values(), key=lambda r: r["profit_abs"], reverse=True)
    for r in rows:
        r["profit_abs"] = round(r["profit_abs"], 8)
        r["staked"] = round(r["staked"], 8)
        # Return on what was actually committed, which is the comparable number
        # across entries of very different size.
        r["profit_pct"] = round(r["profit_abs"] / r["staked"] * 100, 4) if r["staked"] else None
        r["win_rate"] = round(r["wins"] / r["count"] * 100, 2) if r["count"] else None
    return {"by": by, "rows": rows[:limit], "total_groups": len(rows)}


@router.get("/history/equity")
async def history_equity(db: UserDB) -> dict:
    """Account value over time, reconstructed from realised profit.

    freqtrade's own wallet history lives in its tables and was reset with them,
    so it cannot answer this across a redeploy. What survives is every closed
    trade, and the wallet's value *now*. Walking the realised profit backwards
    from the current total gives the value at each close.

    Deliberately realised-only: an unrealised mark would move with the market
    and make a historical series that changes every time you look at it.
    """
    closed = [t for t in _merged_trades(db)
              if not t.get("is_open") and t.get("close_date")
              and t.get("close_profit_abs") is not None]
    closed.sort(key=lambda t: str(t["close_date"]))
    if not closed:
        return {"points": [], "note": "no closed trades yet"}

    running = 0.0
    points = []
    for t in closed:
        running += float(t["close_profit_abs"])
        points.append({"at": t["close_date"], "realised": round(running, 8)})
    return {
        "points": points,
        "realised_total": points[-1]["realised"],
        "from": points[0]["at"],
        "to": points[-1]["at"],
    }


@router.get("/{bot_id}/trades")
async def bot_trades(bot_id: str, db: UserDB, limit: int = 200, open_only: bool = False) -> dict:
    filters = {"bot_instance_id": f"eq.{bot_id}"}
    if open_only:
        filters["is_open"] = "eq.true"
    return {
        "trades": db.select(
            "trade_archive",
            filters=filters,
            order="open_date.desc",
            limit=min(limit, 1000),
        )
    }


@router.get("/{bot_id}/pnl")
async def bot_pnl(bot_id: str, db: UserDB, limit: int = 120) -> dict:
    return {
        "daily": db.select(
            "v_trade_pnl_daily",
            filters={"bot_instance_id": f"eq.{bot_id}"},
            order="day.desc",
            limit=min(limit, 400),
        )
    }
