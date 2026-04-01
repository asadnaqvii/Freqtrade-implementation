"""
Weekly summary job — sent every Monday morning.
"""

from __future__ import annotations

import logging

from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.freqtrade_client import get_freqtrade_client

logger = logging.getLogger(__name__)


async def run_weekly_summary():
    """Send weekly performance summary to each subscribed user."""
    logger.info("Running weekly summary job...")

    wa = WhatsAppClient()
    ft = get_freqtrade_client()
    users = db.get_all_users_with_notifications("weekly")

    try:
        profit = await ft.get_profit()
        balance = await ft.get_balance()
    except Exception as e:
        logger.error("Failed to fetch Freqtrade data for weekly summary: %s", e)
        return

    bal_str = f"${balance.get('total', 0):,.2f}"

    message = (
        f"Weekly Performance Report\n"
        f"{'=' * 28}\n\n"
        f"Total Trades: {profit.get('trade_count', 0)}\n"
        f"Win Rate: {profit.get('win_rate', 0):.1f}%\n\n"
        f"Closed Profit: {profit.get('profit_closed_coin', 0):+.4f} USDT\n"
        f"Total Profit: {profit.get('profit_all_coin', 0):+.4f} USDT\n\n"
        f"Balance: {bal_str}\n\n"
        f"Keep it up! Reply anytime for a live update."
    )

    for user in users:
        try:
            await wa.send_text(user.whatsapp_number, message)
        except Exception as e:
            logger.error("Failed to send weekly summary to %s: %s", user.whatsapp_number, e)

    logger.info("Weekly summary sent to %d users.", len(users))
