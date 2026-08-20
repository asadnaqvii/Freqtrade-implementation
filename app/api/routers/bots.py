"""Deployed bots: registration, heartbeat and live trade history."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

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
