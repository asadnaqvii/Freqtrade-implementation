"""
Trading handlers — force buy/sell, trade status, P&L.
These are primarily called through Claude's tool use, but can also
be invoked directly for notification formatting.
"""

from __future__ import annotations

import logging

from whatsapp_bot.services.freqtrade_client import FreqtradeClient
from whatsapp_bot.utils.formatters import format_open_trades, format_portfolio

logger = logging.getLogger(__name__)


async def get_status_summary(ft: FreqtradeClient) -> str:
    """Get a formatted summary of bot status and open trades."""
    try:
        status = await ft.get_status()
        return format_open_trades(status)
    except Exception as e:
        logger.error("Failed to get status: %s", e)
        return "Unable to fetch bot status right now."


async def get_portfolio_summary(ft: FreqtradeClient) -> str:
    """Get formatted portfolio overview."""
    try:
        balance = await ft.get_balance()
        profit = await ft.get_profit()
        return format_portfolio(balance, profit)
    except Exception as e:
        logger.error("Failed to get portfolio: %s", e)
        return "Unable to fetch portfolio right now."
