"""
Centralized error handling — every failure becomes a WhatsApp notification.
No silent failures allowed.
"""

import logging
import traceback

logger = logging.getLogger(__name__)

# Maps error categories to user-friendly WhatsApp messages.
ERROR_MESSAGES = {
    "freqtrade_unreachable": (
        "Your bot is currently unreachable. Trading is paused. "
        "We're investigating and will notify you when it's back."
    ),
    "kucoin_api_error": (
        "Exchange connection issue. No trades are executing. "
        "Please check your API key on the settings page."
    ),
    "claude_timeout": (
        "I'm having trouble processing your request right now. "
        "Please try again in a moment."
    ),
    "webhook_delivery_failed": (
        "There was an issue delivering a message. "
        "If you're missing updates, please check back shortly."
    ),
    "macro_scan_failed": None,  # silent for first 2 failures, alert on 3rd
    "unknown": (
        "Something unexpected happened. The team has been notified. "
        "Your trades and funds are safe."
    ),
}


def classify_error(exc: Exception) -> str:
    """Classify an exception into an error category."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()

    if "connect" in msg or "unreachable" in msg or "timeout" in name:
        if "kucoin" in msg or "exchange" in msg:
            return "kucoin_api_error"
        if "freqtrade" in msg or "8080" in msg:
            return "freqtrade_unreachable"
    if "anthropic" in msg or "claude" in msg or "overloaded" in msg:
        return "claude_timeout"
    if "webhook" in msg or "whatsapp" in msg:
        return "webhook_delivery_failed"
    return "unknown"


def get_user_message(error_category: str) -> str | None:
    """Return the user-facing message for an error category, or None if silent."""
    return ERROR_MESSAGES.get(error_category, ERROR_MESSAGES["unknown"])


async def handle_error(exc: Exception, whatsapp_client=None, phone_number: str | None = None):
    """
    Log the error and optionally send a WhatsApp alert to the user.
    Called from any service/handler that catches an exception.
    """
    category = classify_error(exc)
    logger.error("Error [%s]: %s\n%s", category, exc, traceback.format_exc())

    user_msg = get_user_message(category)
    if user_msg and whatsapp_client and phone_number:
        try:
            await whatsapp_client.send_text(phone_number, f"⚠️ {user_msg}")
        except Exception as send_err:
            logger.error("Failed to send error alert to %s: %s", phone_number, send_err)
