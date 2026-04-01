"""
Daily summary job — sends P&L from Freqtrade to subscribed users.
"""

from __future__ import annotations

import logging

from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.freqtrade_client import get_freqtrade_client

logger = logging.getLogger(__name__)


async def run_daily_summary():
    """Send daily P&L summary to each subscribed user."""
    logger.info("Running daily summary job...")

    wa = WhatsAppClient()
    ft = get_freqtrade_client()
    users = db.get_all_users_with_notifications("daily")

    try:
        profit = await ft.get_profit()
        balance = await ft.get_balance()
        open_trades = await ft.get_status()
    except Exception as e:
        logger.error("Failed to fetch Freqtrade data for daily summary: %s", e)
        return

    bal_str = f"${balance.get('total', 0):,.2f}"

    message = (
        f"Daily Summary\n"
        f"{'=' * 28}\n\n"
        f"Open Trades: {len(open_trades)}\n"
        f"Total Trades: {profit.get('trade_count', 0)}\n"
        f"Win Rate: {profit.get('win_rate', 0):.1f}%\n\n"
        f"Today's Profit: {profit.get('profit_closed_coin', 0):+.4f} USDT\n"
        f"Total Profit: {profit.get('profit_all_coin', 0):+.4f} USDT\n\n"
        f"Balance: {bal_str}\n\n"
        f"Ask me anything — \"How am I doing?\" or \"Show my trades\""
    )

    for user in users:
        try:
            await wa.send_text(user.whatsapp_number, message)
        except Exception as e:
            logger.error("Failed to send daily summary to %s: %s", user.whatsapp_number, e)

    logger.info("Daily summary sent to %d users.", len(users))
