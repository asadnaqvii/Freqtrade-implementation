"""What the strategy builder offers, and which venues can be connected."""

from __future__ import annotations

from fastapi import APIRouter

from app.providers import registry
from app.strategy_builder import catalog
from app.strategy_builder.spec import COMPARISONS, TIMEFRAMES

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/indicators")
async def indicators() -> dict:
    """The closed vocabulary a spec may use.

    Public because it is a schema, not data: the builder UI needs it before the
    user has signed in, and it reveals nothing about anyone's account.
    """
    return {
        "indicators": catalog.as_json(),
        "operators": list(COMPARISONS),
        "timeframes": list(TIMEFRAMES),
        "ohlcv_columns": list(catalog.OHLCV_COLUMNS),
        "spec_version": "1.0",
    }


@router.get("/exchanges")
async def exchanges() -> dict:
    """Every venue backtesting and verification work against."""
    return {"exchanges": registry.available()}
