"""FastAPI dependencies: authentication and per-request Supabase clients."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status

from app.core.config import ConfigError, get_settings
from app.core.security import AuthError, Principal, bearer_token, principal_from_token
from app.core.supabase import SupabaseClient


def current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    try:
        return principal_from_token(bearer_token(authorization))
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except ConfigError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc


def user_client(
    principal: Annotated[Principal, Depends(current_principal)],
) -> SupabaseClient:
    """A client that carries the caller's own token.

    Every user-facing query goes through this so RLS applies. If a handler
    forgets an owner filter the database still refuses -- the policy is the
    backstop, not the handler.
    """
    return SupabaseClient.as_user(principal.token)


def service_client() -> SupabaseClient:
    """A client that bypasses RLS. Only for work not on behalf of a user."""
    try:
        return SupabaseClient.service()
    except ConfigError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


CurrentUser = Annotated[Principal, Depends(current_principal)]
UserDB = Annotated[SupabaseClient, Depends(user_client)]
ServiceDB = Annotated[SupabaseClient, Depends(service_client)]
