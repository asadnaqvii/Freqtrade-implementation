"""
User authentication and registration.
First message from a phone number → auto-register as trader.
Admin privileges set manually in Supabase.
"""

from __future__ import annotations

import logging

from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.db.models import User

logger = logging.getLogger(__name__)


async def get_or_create_user(phone: str) -> User:
    """Look up user by phone; create if first contact."""
    user = db.get_user_by_phone(phone)
    if user:
        return user

    logger.info("New user detected: %s — creating account", phone)
    return db.create_user(phone)


def is_admin(user: User) -> bool:
    return user.role == "admin"


def is_onboarded(user: User) -> bool:
    return user.onboarded_at is not None


def require_admin(user: User) -> bool:
    """Check if user has admin privileges. Returns True if admin."""
    if not is_admin(user):
        logger.warning("Non-admin user %s attempted admin action", user.whatsapp_number)
        return False
    return True
