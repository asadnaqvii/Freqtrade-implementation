"""What the strategy builder offers, and which venues can be connected."""

from __future__ import annotations

import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException

from app.api.deps import CurrentUser
from app.providers import registry
from app.providers.base import ProviderError
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


@router.get("/history")
async def history(
    _: CurrentUser,
    exchange: str = "kucoin",
    pairs: str = "",
    timeframe: str = "1d",
) -> dict:
    """How far back this venue's candles actually go, per pair.

    "Backtest ten years" is a reasonable thing to want and frequently impossible:
    KuCoin did not exist before 2017, and most altcoins listed years after that.
    Asking the venue is the only honest answer, and it is public market data, so
    no credentials are involved.
    """
    wanted = [p.strip().upper() for p in pairs.split(",") if p.strip()][:12]
    if not wanted:
        raise HTTPException(status_code=422, detail="name at least one pair")

    provider = registry.build({"provider": exchange.lower(), "ccxt_id": exchange.lower()})
    out = []
    try:
        for pair in wanted:
            try:
                earliest = provider.earliest_candle(pair, timeframe)
            except ProviderError as exc:
                # Include the type: an empty str(exc) told us nothing last time.
                detail = str(exc).strip() or exc.__class__.__name__
                out.append({"pair": pair, "error": detail})
                continue
            except Exception as exc:  # noqa: BLE001 - one bad pair must not sink the rest
                out.append({"pair": pair,
                            "error": f"{exc.__class__.__name__}: {str(exc)[:160]}"})
                continue
            out.append({
                "pair": pair,
                "earliest": earliest.isoformat() if earliest else None,
                "years": (
                    round((datetime.now(timezone.utc) - earliest).days / 365.25, 1)
                    if earliest else None
                ),
            })
    finally:
        provider.close()

    usable = [row for row in out if row.get("earliest")]
    return {
        "exchange": exchange,
        "timeframe": timeframe,
        "pairs": out,
        # A backtest spans the pairs together, so the shortest history is what
        # actually limits the window.
        "common_start": max((row["earliest"] for row in usable), default=None),
    }


#: Loading an exchange's markets is a real HTTP call returning thousands of
#: entries, and the answer barely changes day to day. Cache it per exchange so
#: filling a dropdown does not hammer the venue.
_MARKET_CACHE: dict[str, tuple[float, list[dict]]] = {}
_MARKET_TTL_SECONDS = 3600


@router.get("/markets")
async def markets(_: CurrentUser, exchange: str = "kucoin", quote: str = "") -> dict:
    """Tradable spot pairs on a venue, for picking rather than typing.

    Typing pair symbols by hand is how you discover, after a backtest returns
    nothing, that this venue spells it XBT/USD.
    """
    venue = exchange.strip().lower()
    if not venue:
        raise HTTPException(status_code=422, detail="name an exchange")

    cached = _MARKET_CACHE.get(venue)
    if cached and (time.time() - cached[0]) < _MARKET_TTL_SECONDS:
        rows = cached[1]
    else:
        provider = registry.build({"provider": venue, "ccxt_id": venue})
        try:
            found = provider.fetch_markets()
        except ProviderError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        finally:
            provider.close()
        rows = sorted(
            (
                {"symbol": m.symbol, "base": m.base, "quote": m.quote,
                 "min_cost": m.min_cost}
                for m in found.values()
                if m.spot and m.active and m.base and m.quote
            ),
            key=lambda m: (m["quote"], m["base"]),
        )
        _MARKET_CACHE[venue] = (time.time(), rows)

    quotes = sorted({row["quote"] for row in rows})
    wanted = quote.strip().upper()
    if wanted:
        rows = [row for row in rows if row["quote"] == wanted]

    return {
        "exchange": venue,
        "quotes": quotes,
        "count": len(rows),
        # Enough for a searchable list without shipping every market on Binance.
        "pairs": rows[:1500],
    }
