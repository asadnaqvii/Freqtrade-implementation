"""Live trading: what the bot is doing right now, and the one control over it.

Everything here reads through the bot rather than the database, because the
numbers only exist live. An open position's profit depends on the current price;
the database records what the position cost, not what it is worth.

Closed history is a database question and lives in bots.py. This router is for
the present tense.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, UserDB
from app.bot_api import BotClient, BotError
from app.bot_api.client import BotNotConfigured, BotUnreachable
from app.core.config import get_settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])


def _client_for_caller(db) -> BotClient:
    """The bot this caller owns -- and nobody else's.

    Authentication is not authorization. A valid token only proves someone
    signed up; on a project with open registration that is anyone at all. The
    bot must therefore be looked up through the caller's own RLS-scoped client,
    so the database decides whether they may see it. Reading the bot's address
    from process settings instead would hand every signed-up account the live
    positions, the wallet, and the button that closes a position.
    """
    try:
        owned = db.select(
            "bot_instances",
            columns="id,name,api_base_url",
            filters={"api_base_url": "not.is.null"},
            order="last_heartbeat_at.desc",
            limit=1,
        )
    except Exception as exc:  # noqa: BLE001 - a lookup failure must not open the door
        raise HTTPException(status_code=502, detail="could not resolve your bot") from exc

    if not owned:
        # Deliberately the same answer as "no such bot": someone else's bot must
        # not be distinguishable from one that does not exist.
        raise HTTPException(
            status_code=404,
            detail="you have no bot with a reachable address. Deploy one, or set "
                   "FREQTRADE_API_BASE_URL on the bot service so it registers where "
                   "it can be reached.",
        )

    settings = get_settings()
    return BotClient(
        owned[0]["api_base_url"],
        settings.bot.api_username,
        settings.bot.api_password,
    )


def _handle(exc: BotError):
    # Unreachable is a 503: the request was fine, the bot is not there. Anything
    # else the bot actually answered, so it is a bad gateway.
    status = 503 if isinstance(exc, BotUnreachable) else 502
    return HTTPException(status_code=status, detail=str(exc))


class ForceExitRequest(BaseModel):
    trade_id: str = Field(min_length=1, max_length=40)
    order_type: str | None = Field(default=None, pattern="^(market|limit)$")
    amount: float | None = Field(default=None, gt=0)


@router.get("/overview")
async def overview(db: UserDB) -> dict:
    """One call for the whole live page.

    Assembled server-side because the browser would otherwise make seven
    requests across the private network on every refresh, and a partial failure
    would leave the page in an ambiguous half-state.
    """
    try:
        return _client_for_caller(db).overview()
    except BotError as exc:
        raise _handle(exc) from exc


@router.get("/candles")
async def candles(
    db: UserDB,
    pair: str,
    timeframe: str = "",
    limit: int = 500,
) -> dict:
    """OHLCV for one pair, with the strategy's own entry and exit signals.

    Declared before the generic /{section} read: a literal path after a
    parameterised one never gets reached.
    """
    client = _client_for_caller(db)
    try:
        config = client.get("show_config") or {}
        payload = client.get("pair_candles", {
            "pair": pair,
            "timeframe": timeframe or config.get("timeframe") or "5m",
            "limit": max(50, min(limit, 1500)),
        })
    except BotError as exc:
        raise _handle(exc) from exc
    return payload if isinstance(payload, dict) else {"data": payload}


@router.get("/{section}")
async def section(section: str, db: UserDB) -> dict:
    """Any single read the bot permits, for panels that refresh on their own."""
    client = _client_for_caller(db)
    if section not in client.READS:
        raise HTTPException(
            status_code=404,
            detail=f"no such section; available: {', '.join(sorted(client.READS))}",
        )
    try:
        payload = client.get(section)
    except BotError as exc:
        raise _handle(exc) from exc
    # freqtrade returns a bare list for some endpoints; keep the envelope
    # uniform so the UI never has to branch on the shape.
    return payload if isinstance(payload, dict) else {"data": payload}


class ControlRequest(BaseModel):
    action: str = Field(pattern="^(start|stop|stopentry|reload_config)$")


@router.post("/control")
async def control(body: ControlRequest, user: CurrentUser, db: UserDB) -> dict:
    """Start, stop, stop-entry or reload the bot.

    Logged with who asked. "Why did the bot stop overnight" is a question that
    gets asked later and deserves an answer that is not a shrug.
    """
    log.warning("bot control %s requested by %s", body.action, user.profile_id)
    try:
        result = _client_for_caller(db).act(body.action)
    except BotError as exc:
        raise _handle(exc) from exc
    return {"result": result}


@router.post("/forceexit")
async def force_exit(body: ForceExitRequest, user: CurrentUser, db: UserDB) -> dict:
    """Close an open position now, at market unless told otherwise.

    The only state-changing call the app can make against the bot. Logged with
    who asked, because "why did this position close" is a question that gets
    asked later and deserves an answer.
    """
    log.warning("force exit requested by %s for trade %s", user.profile_id, body.trade_id)
    try:
        result = _client_for_caller(db).force_exit(
            body.trade_id, order_type=body.order_type, amount=body.amount
        )
    except BotError as exc:
        raise _handle(exc) from exc
    return {"result": result}
