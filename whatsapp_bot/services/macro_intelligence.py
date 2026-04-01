"""
Macro intelligence — CryptoPanic, NewsAPI, Fear & Greed Index.
"""

from __future__ import annotations

import logging

import httpx

from whatsapp_bot.config import settings

logger = logging.getLogger(__name__)


class MacroIntelligence:
    """Fetches macro data from external APIs."""

    # ── CryptoPanic ────────────────────────────────────────

    async def fetch_crypto_headlines(self, limit: int = 10) -> list[str]:
        """Fetch latest crypto news headlines from CryptoPanic."""
        if not settings.cryptopanic_api_key:
            logger.warning("CryptoPanic API key not set — skipping.")
            return []

        url = "https://cryptopanic.com/api/v1/posts/"
        params = {
            "auth_token": settings.cryptopanic_api_key,
            "kind": "news",
            "filter": "important",
            "public": "true",
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                data = resp.json()
                results = data.get("results", [])
                return [r.get("title", "") for r in results[:limit]]
        except Exception as e:
            logger.error("CryptoPanic fetch failed: %s", e)
            return []

    # ── Fear & Greed Index ─────────────────────────────────

    async def fetch_fear_greed(self) -> int | None:
        """Fetch the current Crypto Fear & Greed Index score (0-100)."""
        url = "https://api.alternative.me/fng/?limit=1"

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                data = resp.json()
                entries = data.get("data", [])
                if entries:
                    return int(entries[0].get("value", 50))
                return None
        except Exception as e:
            logger.error("Fear & Greed fetch failed: %s", e)
            return None

    # ── NewsAPI (macro headlines) ──────────────────────────

    async def fetch_macro_headlines(self, limit: int = 5) -> list[str]:
        """Fetch macro news headlines (Fed, economy, geopolitical)."""
        if not settings.newsapi_key:
            logger.warning("NewsAPI key not set — skipping.")
            return []

        url = "https://newsapi.org/v2/everything"
        params = {
            "apiKey": settings.newsapi_key,
            "q": "cryptocurrency OR bitcoin OR federal reserve OR inflation",
            "language": "en",
            "sortBy": "publishedAt",
            "pageSize": limit,
        }

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(url, params=params)
                resp.raise_for_status()
                articles = resp.json().get("articles", [])
                return [a.get("title", "") for a in articles[:limit]]
        except Exception as e:
            logger.error("NewsAPI fetch failed: %s", e)
            return []

    # ── Combined Scan ──────────────────────────────────────

    async def scan(self) -> dict:
        """Run a full macro scan — returns headlines + fear/greed score."""
        crypto_headlines = await self.fetch_crypto_headlines()
        macro_headlines = await self.fetch_macro_headlines()
        fear_greed = await self.fetch_fear_greed()

        all_headlines = crypto_headlines + macro_headlines

        # Determine if this scan is significant
        significant = False
        if fear_greed is not None:
            if fear_greed <= settings.fear_greed_alert_low or fear_greed >= settings.fear_greed_alert_high:
                significant = True

        return {
            "headlines": all_headlines,
            "fear_greed_score": fear_greed,
            "significance_triggered": significant,
        }
