"""
Market analysis — live prices and technical indicators via CCXT.
"""

from __future__ import annotations

import logging
from typing import Optional

import ccxt.async_support as ccxt

logger = logging.getLogger(__name__)


class MarketAnalysis:
    """Fetch live market data and compute basic indicators."""

    def __init__(self):
        self.exchange = ccxt.kucoin({"enableRateLimit": True})

    async def get_price(self, pair: str) -> dict:
        """Get current price and 24h stats for a pair."""
        try:
            ticker = await self.exchange.fetch_ticker(pair)
            return {
                "pair": pair,
                "price": ticker.get("last"),
                "high_24h": ticker.get("high"),
                "low_24h": ticker.get("low"),
                "change_pct_24h": ticker.get("percentage"),
                "volume_24h": ticker.get("baseVolume"),
            }
        finally:
            await self.exchange.close()

    async def analyze(self, pair: str, timeframe: str = "5m", limit: int = 100) -> dict:
        """Run technical analysis on a pair."""
        try:
            ohlcv = await self.exchange.fetch_ohlcv(pair, timeframe, limit=limit)

            if len(ohlcv) < 30:
                return {"pair": pair, "error": "Not enough data for analysis"}

            closes = [c[4] for c in ohlcv]
            volumes = [c[5] for c in ohlcv]
            current_price = closes[-1]

            # RSI (14-period)
            rsi = self._calc_rsi(closes, 14)

            # EMAs
            ema_50 = self._calc_ema(closes, 50)
            ema_200 = self._calc_ema(closes, 200)

            # Bollinger Bands (20, 2)
            bb = self._calc_bollinger(closes, 20, 2)

            # Volume average
            vol_avg = sum(volumes[-20:]) / 20 if len(volumes) >= 20 else sum(volumes) / len(volumes)
            vol_current = volumes[-1]

            # Trend determination
            trend = "Sideways"
            if ema_50 and ema_200:
                if current_price > ema_50 > ema_200:
                    trend = "Bullish"
                elif current_price < ema_50 < ema_200:
                    trend = "Bearish"

            # RSI interpretation
            rsi_status = "Neutral"
            if rsi:
                if rsi < 30:
                    rsi_status = "Oversold"
                elif rsi > 70:
                    rsi_status = "Overbought"

            return {
                "pair": pair,
                "price": current_price,
                "trend": trend,
                "rsi": round(rsi, 1) if rsi else None,
                "rsi_status": rsi_status,
                "ema_50": round(ema_50, 2) if ema_50 else None,
                "ema_200": round(ema_200, 2) if ema_200 else None,
                "bb_upper": round(bb["upper"], 2) if bb else None,
                "bb_lower": round(bb["lower"], 2) if bb else None,
                "bb_position": self._bb_position(current_price, bb) if bb else None,
                "volume_vs_avg": round(vol_current / vol_avg, 2) if vol_avg > 0 else None,
            }
        finally:
            await self.exchange.close()

    # ── Indicator Calculations ─────────────────────────────

    @staticmethod
    def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
        if len(closes) < period + 1:
            return None
        deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
        gains = [d if d > 0 else 0 for d in deltas]
        losses = [-d if d < 0 else 0 for d in deltas]

        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period

        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period

        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    @staticmethod
    def _calc_ema(closes: list[float], period: int) -> float | None:
        if len(closes) < period:
            return None
        multiplier = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for price in closes[period:]:
            ema = (price - ema) * multiplier + ema
        return ema

    @staticmethod
    def _calc_bollinger(closes: list[float], period: int = 20, std_dev: int = 2) -> dict | None:
        if len(closes) < period:
            return None
        window = closes[-period:]
        mean = sum(window) / period
        variance = sum((x - mean) ** 2 for x in window) / period
        std = variance ** 0.5
        return {"upper": mean + std_dev * std, "middle": mean, "lower": mean - std_dev * std}

    @staticmethod
    def _bb_position(price: float, bb: dict) -> str:
        if price >= bb["upper"]:
            return "At/above upper band"
        elif price <= bb["lower"]:
            return "At/below lower band"
        else:
            mid = bb["middle"]
            if price > mid:
                return "Between middle and upper"
            else:
                return "Between lower and middle"
