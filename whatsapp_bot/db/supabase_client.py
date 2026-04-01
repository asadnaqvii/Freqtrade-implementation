"""
Supabase client — CRUD for users, strategies, conversations, macro_events, trade_logs.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

from supabase import create_client, Client

from whatsapp_bot.config import settings
from whatsapp_bot.db.models import (
    User, Strategy, ConversationMessage, MacroEvent, TradeLog,
)

logger = logging.getLogger(__name__)

_client: Client | None = None


def get_client() -> Client:
    global _client
    if _client is None:
        _client = create_client(settings.supabase_url, settings.supabase_key)
    return _client


# ── Users ──────────────────────────────────────────────────

def get_user_by_phone(phone: str) -> User | None:
    res = get_client().table("users").select("*").eq("whatsapp_number", phone).execute()
    if res.data:
        return User(**res.data[0])
    return None


def create_user(phone: str) -> User:
    data = {"whatsapp_number": phone, "role": "trader"}
    res = get_client().table("users").insert(data).execute()
    return User(**res.data[0])


def update_user(user_id: str, updates: dict) -> User:
    res = get_client().table("users").update(updates).eq("id", user_id).execute()
    return User(**res.data[0])


def get_all_users_with_notifications(notification_type: str) -> list[User]:
    """Get all onboarded users who have a specific notification enabled."""
    res = get_client().table("users").select("*").not_.is_("onboarded_at", "null").execute()
    users = []
    for row in res.data:
        config = row.get("notifications_config", {})
        if config.get(notification_type, False):
            users.append(User(**row))
    return users


# ── Strategies ─────────────────────────────────────────────

def get_active_strategy(user_id: str) -> Strategy | None:
    res = (
        get_client()
        .table("strategies")
        .select("*")
        .eq("user_id", user_id)
        .eq("is_active", True)
        .execute()
    )
    if res.data:
        return Strategy(**res.data[0])
    return None


def create_strategy(user_id: str, strategy_name: str, params: dict, reason: str, activated_by: str = "bot") -> Strategy:
    # Deactivate any existing active strategy for this user
    get_client().table("strategies").update({"is_active": False}).eq("user_id", user_id).eq("is_active", True).execute()

    data = {
        "user_id": user_id,
        "strategy_name": strategy_name,
        "stoploss_pct": params.get("stoploss_pct", -5.0),
        "max_open_trades": params.get("max_open_trades", 3),
        "timeframe": params.get("timeframe", "5m"),
        "roi_config": params.get("roi_config", {"0": 0.05}),
        "activated_by": activated_by,
        "reason": reason,
        "is_active": True,
    }
    res = get_client().table("strategies").insert(data).execute()
    return Strategy(**res.data[0])


def update_strategy(strategy_id: str, updates: dict) -> Strategy:
    res = get_client().table("strategies").update(updates).eq("id", strategy_id).execute()
    return Strategy(**res.data[0])


# ── Conversations ──────────────────────────────────────────

def save_message(user_id: str, role: str, message: str, metadata: dict | None = None) -> None:
    get_client().table("conversations").insert({
        "user_id": user_id,
        "role": role,
        "message": message,
        "metadata": metadata or {},
    }).execute()


def get_recent_messages(user_id: str, limit: int = 20) -> list[ConversationMessage]:
    res = (
        get_client()
        .table("conversations")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    messages = [ConversationMessage(**row) for row in res.data]
    messages.reverse()  # oldest first
    return messages


# ── Macro Events ──────────────────────────────────────────

def save_macro_event(event: dict) -> MacroEvent:
    res = get_client().table("macro_events").insert(event).execute()
    return MacroEvent(**res.data[0])


def get_latest_macro_event() -> MacroEvent | None:
    res = (
        get_client()
        .table("macro_events")
        .select("*")
        .order("scanned_at", desc=True)
        .limit(1)
        .execute()
    )
    if res.data:
        return MacroEvent(**res.data[0])
    return None


def update_macro_event(event_id: str, updates: dict) -> None:
    get_client().table("macro_events").update(updates).eq("id", event_id).execute()


# ── Trade Logs ────────────────────────────────────────────

def save_trade_log(log: dict) -> None:
    get_client().table("trade_logs").insert(log).execute()


def get_trade_logs(user_id: str, limit: int = 50) -> list[TradeLog]:
    res = (
        get_client()
        .table("trade_logs")
        .select("*")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(limit)
        .execute()
    )
    return [TradeLog(**row) for row in res.data]


