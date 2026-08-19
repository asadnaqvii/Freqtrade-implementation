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
