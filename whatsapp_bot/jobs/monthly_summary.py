"""
Monthly summary job — sent on the 1st of each month.
"""

from __future__ import annotations

import logging

from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.freqtrade_client import get_freqtrade_client

logger = logging.getLogger(__name__)


async def run_monthly_summary():
    """Send monthly performance summary to each subscribed user."""
    logger.info("Running monthly summary job...")

    wa = WhatsAppClient()
    ft = get_freqtrade_client()
    users = db.get_all_users_with_notifications("monthly")

    try:
        profit = await ft.get_profit()
        balance = await ft.get_balance()
    except Exception as e:
        logger.error("Failed to fetch Freqtrade data for monthly summary: %s", e)
        return

    bal_str = f"${balance.get('total', 0):,.2f}"

    for user in users:
        try:
            strategy = db.get_active_strategy(user.id)
            strat_name = strategy.strategy_name if strategy else "None"

            message = (
                f"Monthly Performance Report\n"
                f"{'=' * 28}\n\n"
                f"Strategy: {strat_name}\n"
                f"Total Trades: {profit.get('trade_count', 0)}\n"
                f"Win Rate: {profit.get('win_rate', 0):.1f}%\n\n"
                f"Closed Profit: {profit.get('profit_closed_coin', 0):+.4f} USDT\n"
                f"Total Profit: {profit.get('profit_all_coin', 0):+.4f} USDT\n\n"
                f"Balance: {bal_str}\n\n"
                f"Consider reviewing your strategy if performance "
                f"doesn't match your {user.yield_target_pct}% monthly target."
            )

            await wa.send_text(user.whatsapp_number, message)
        except Exception as e:
            logger.error("Failed to send monthly summary to %s: %s", user.whatsapp_number, e)

    logger.info("Monthly summary sent to %d users.", len(users))
