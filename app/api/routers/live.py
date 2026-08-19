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

from app.api.deps import CurrentUser
from app.bot_api import BotClient, BotError
from app.bot_api.client import BotNotConfigured, BotUnreachable

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/live", tags=["live"])


def _client() -> BotClient:
    try:
        return BotClient.from_settings()
    except BotNotConfigured as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


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
async def overview(_: CurrentUser) -> dict:
    """One call for the whole live page.

    Assembled server-side because the browser would otherwise make seven
    requests across the private network on every refresh, and a partial failure
    would leave the page in an ambiguous half-state.
    """
    try:
        return _client().overview()
    except BotError as exc:
        raise _handle(exc) from exc


@router.get("/{section}")
async def section(section: str, _: CurrentUser) -> dict:
    """Any single read the bot permits, for panels that refresh on their own."""
    client = _client()
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


@router.post("/forceexit")
async def force_exit(body: ForceExitRequest, user: CurrentUser) -> dict:
    """Close an open position now, at market unless told otherwise.

    The only state-changing call the app can make against the bot. Logged with
    who asked, because "why did this position close" is a question that gets
    asked later and deserves an answer.
    """
    log.warning("force exit requested by %s for trade %s", user.profile_id, body.trade_id)
    try:
        result = _client().force_exit(
            body.trade_id, order_type=body.order_type, amount=body.amount
        )
    except BotError as exc:
        raise _handle(exc) from exc
    return {"result": result}
