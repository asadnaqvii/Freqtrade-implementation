"""Liveness and configuration self-check."""

from __future__ import annotations

from fastapi import APIRouter

from app.core.config import get_settings

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
async def health() -> dict:
    """Report what is configured, without disclosing any of it.

    Useful immediately after a deploy: it answers "did the env vars land"
    without needing a shell on the box.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "supabase_configured": settings.supabase.configured,
        "jwt_verification": bool(settings.supabase.jwt_secret),
        "database_url_set": bool(settings.supabase.db_url),
        "bot": {
            "name": settings.bot.name,
            "exchange": settings.bot.exchange,
            "db_schema": settings.bot.db_schema,
            "api_reachable_at": bool(settings.bot.api_base_url),
        },
    }


@router.get("/config")
async def public_config() -> dict:
    """Bootstrap values the browser needs.

    Only the publishable anon key is ever sent here. The service role key stays
    on the server; handing it to a browser would defeat every RLS policy at once.
    """
    settings = get_settings()
    return {
        "supabase_url": settings.supabase.url,
        "supabase_anon_key": settings.supabase.anon_key,
    }
