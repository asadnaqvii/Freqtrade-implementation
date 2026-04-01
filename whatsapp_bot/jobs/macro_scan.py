"""
Macro scan job — runs every 6 hours, triggers alerts on significant events.
"""

from __future__ import annotations

import logging

from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.claude_engine import ClaudeEngine
from whatsapp_bot.services.macro_intelligence import MacroIntelligence
from whatsapp_bot.utils.formatters import format_macro_alert

logger = logging.getLogger(__name__)


async def run_macro_scan():
    """Scan macro conditions and alert users if significant."""
    logger.info("Running macro scan...")

    macro = MacroIntelligence()
    scan_result = await macro.scan()

    headlines = scan_result["headlines"]
    fear_greed = scan_result["fear_greed_score"]
    significant = scan_result["significance_triggered"]

    # Save scan to DB
    event_data = {
        "headlines": headlines,
        "fear_greed_score": fear_greed,
        "significance_triggered": significant,
    }

    if not significant:
        event_data["claude_recommendation"] = None
        event_data["alert_sent"] = False
        db.save_macro_event(event_data)
        logger.info("Macro scan complete — no significant events. F&G: %s", fear_greed)
        return

    # Significant event detected — ask Claude for analysis
    logger.info("Significant macro event detected! F&G: %s. Running Claude analysis...", fear_greed)

    claude = ClaudeEngine()

    # Get all active users and their strategies for personalized alerts
    users = db.get_all_users_with_notifications("macro_alerts")

    for user in users:
        strategy = db.get_active_strategy(user.id)
        if not strategy:
            continue

        try:
            recommendation = claude.analyze_macro(headlines, fear_greed, strategy)

            if "NO_ADJUSTMENT_NEEDED" in recommendation:
                logger.info("Claude says no adjustment needed for user %s", user.whatsapp_number)
                continue

            # Save event with recommendation
            event_data["claude_recommendation"] = recommendation
            event_data["alert_sent"] = True
            saved_event = db.save_macro_event(event_data)

            # Format and send alert
            alert_msg = format_macro_alert({
                "fear_greed_score": fear_greed,
                "headlines": headlines,
                "claude_recommendation": recommendation,
            })

            wa = WhatsAppClient()
            await wa.send_text(user.whatsapp_number, alert_msg)

            # Save conversation
            db.save_message(user.id, "assistant", alert_msg, {"type": "macro_alert", "event_id": str(saved_event.id)})

            logger.info("Macro alert sent to %s", user.whatsapp_number)

        except Exception as e:
            logger.error("Failed to process macro alert for %s: %s", user.whatsapp_number, e)

    logger.info("Macro scan complete.")
