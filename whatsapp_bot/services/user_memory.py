"""
User memory and context management.
Loads user profile + active strategy + recent messages for Claude's context.
"""

from __future__ import annotations

import logging

from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.db.models import User, Strategy, ConversationMessage

logger = logging.getLogger(__name__)


class UserMemory:
    """Loads and manages per-user context for the AI engine."""

    def __init__(self, user: User):
        self.user = user

    def get_active_strategy(self) -> Strategy | None:
        return db.get_active_strategy(self.user.id)

    def get_recent_messages(self, limit: int = 20) -> list[ConversationMessage]:
        return db.get_recent_messages(self.user.id, limit=limit)

    def save_message(self, role: str, message: str, metadata: dict | None = None) -> None:
        db.save_message(self.user.id, role, message, metadata)

    def save_user_and_assistant(self, user_msg: str, assistant_msg: str) -> None:
        """Save both sides of a conversation turn."""
        db.save_message(self.user.id, "user", user_msg)
        db.save_message(self.user.id, "assistant", assistant_msg)
