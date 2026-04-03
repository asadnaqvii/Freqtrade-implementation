"""
Strategy manager — CRUD operations on strategies.
Per-user strategy configs stored in Supabase + synced to user's Freqtrade instance.
"""

from __future__ import annotations

import logging
from typing import Optional

from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.freqtrade_client import FreqtradeClient

logger = logging.getLogger(__name__)

# Default parameters per strategy (matches strategies/*.py files)
STRATEGY_DEFAULTS = {
    "ConservativeRSI": {"stoploss_pct": -5.0, "max_open_trades": 2, "timeframe": "5m", "roi_config": {"0": 0.15, "30": 0.10, "60": 0.05, "120": 0.02}},
    "EMACrossover": {"stoploss_pct": -7.0, "max_open_trades": 3, "timeframe": "15m", "roi_config": {"0": 0.20, "40": 0.15, "100": 0.10, "180": 0.05}},
    "BollingerBreakout": {"stoploss_pct": -8.0, "max_open_trades": 4, "timeframe": "5m", "roi_config": {"0": 0.25, "30": 0.18, "80": 0.12, "150": 0.08}},
    "ActiveTrader": {"stoploss_pct": -3.0, "max_open_trades": 5, "timeframe": "1m", "roi_config": {"0": 0.03, "15": 0.02, "30": 0.01, "60": 0.005}},
    "MeanReversionScalper": {"stoploss_pct": -0.4, "max_open_trades": 6, "timeframe": "5m", "roi_config": {"0": 0.005}},
    "EMACrossoverScalper": {"stoploss_pct": -0.5, "max_open_trades": 3, "timeframe": "5m", "roi_config": {"0": 0.006}},
}


class StrategyManager:
    """Manages per-user strategy selection and parameter updates."""

    async def switch_strategy(
        self,
        user_id: str,
        strategy_name: str,
        reason: str,
        activated_by: str = "user",
        ft_client: Optional[FreqtradeClient] = None,
    ) -> str:
        """Switch to a new strategy — save to DB and reload Freqtrade if connected."""
        from whatsapp_bot.config import settings

        if strategy_name not in settings.available_strategies:
            return f"Unknown strategy: {strategy_name}. Available: {', '.join(settings.available_strategies)}"

        params = STRATEGY_DEFAULTS.get(strategy_name, STRATEGY_DEFAULTS["MeanReversionScalper"])

        db.create_strategy(
            user_id=user_id,
            strategy_name=strategy_name,
            params=params,
            reason=reason,
            activated_by=activated_by,
        )

        # Reload Freqtrade config if user has a connected instance
        if ft_client:
            try:
                await ft_client.reload_config()
                logger.info("Freqtrade config reloaded for strategy switch to %s", strategy_name)
            except Exception as e:
                logger.warning("Failed to reload Freqtrade config: %s", e)
                return "Strategy saved but Freqtrade reload failed — it will pick up changes on next restart."

        return "Strategy activated successfully."

    async def update_params(
        self,
        user_id: str,
        updates: dict,
        ft_client: Optional[FreqtradeClient] = None,
    ) -> str:
        """Update parameters on the current active strategy."""
        strategy = db.get_active_strategy(user_id)
        if not strategy:
            return "No active strategy found."

        db_updates = {}
        if "stoploss_pct" in updates:
            db_updates["stoploss_pct"] = updates["stoploss_pct"]
        if "max_open_trades" in updates:
            db_updates["max_open_trades"] = updates["max_open_trades"]
        if "timeframe" in updates:
            db_updates["timeframe"] = updates["timeframe"]

        if db_updates:
            db.update_strategy(strategy.id, db_updates)

        # Reload Freqtrade config if connected
        if ft_client:
            try:
                await ft_client.reload_config()
            except Exception as e:
                logger.warning("Failed to reload Freqtrade config: %s", e)
                return "Parameters saved but Freqtrade reload failed."

        return "Parameters updated successfully."

    @staticmethod
    def get_strategy_summary(strategy_name: str) -> str:
        """Return a human-readable summary of a strategy."""
        summaries = {
            "ConservativeRSI": "Low risk | 5m | RSI < 30 + 200 EMA filter | -5% SL | 5-15% monthly",
            "EMACrossover": "Low-Medium risk | 15m | EMA golden cross | -7% SL | 10-25% monthly",
            "BollingerBreakout": "Medium risk | 5m | BB + Stochastic breakout | -8% SL | 15-35% monthly",
            "ActiveTrader": "Medium-High risk | 1m | Fast signals | -3% SL | High activity",
            "MeanReversionScalper": "Medium risk | 5m | BB + RSI scalping | -0.4% SL | Tight TP/SL",
        }
        return summaries.get(strategy_name, "Unknown strategy")
