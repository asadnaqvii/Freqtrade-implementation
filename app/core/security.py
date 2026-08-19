"""Request authentication.

Supabase signs its access tokens with the project's JWT secret (HS256). The API
verifies that signature itself rather than calling out to /auth/v1/user on every
request: it is one HMAC instead of a network round trip, and it fails closed.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWTError

from app.core.config import ConfigError, get_settings

log = logging.getLogger(__name__)

# Supabase issues tokens with aud="authenticated" for signed-in users.
_EXPECTED_AUDIENCE = "authenticated"


class AuthError(Exception):
    """Token missing, malformed, expired or not trustworthy."""


@dataclass(frozen=True)
class Principal:
    """Who is making this request."""

    profile_id: str
    email: str | None
    role: str
    token: str
    claims: dict[str, Any]

    @property
    def is_service(self) -> bool:
        return self.role == "service_role"


def decode_token(token: str) -> dict[str, Any]:
    """Verify and decode a Supabase access token.

    Raises AuthError for anything that is not a valid, unexpired token signed by
    this project.
    """
    settings = get_settings()
    secret = settings.supabase.jwt_secret
    if not secret:
        raise ConfigError(
            "SUPABASE_JWT_SECRET is not set, so access tokens cannot be verified. "
            "Find it under Project Settings -> API -> JWT Secret."
        )

    try:
        claims = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            audience=_EXPECTED_AUDIENCE,
            options={"require": ["exp", "sub"]},
        )
    except PyJWTError as exc:
        # Deliberately vague to the caller; the detail goes to the log only.
        log.info("rejected token: %s", exc)
        raise AuthError("invalid or expired token") from exc

    if claims.get("exp", 0) < time.time():
        raise AuthError("token expired")

    subject = claims.get("sub")
    if not subject:
        raise AuthError("token has no subject")

    return claims


def principal_from_token(token: str) -> Principal:
    claims = decode_token(token)
    return Principal(
        profile_id=str(claims["sub"]),
        email=claims.get("email"),
        role=str(claims.get("role", "authenticated")),
        token=token,
        claims=claims,
    )


def bearer_token(authorization: str | None) -> str:
    """Pull the raw token out of an Authorization header."""
    if not authorization:
        raise AuthError("missing Authorization header")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AuthError("expected 'Authorization: Bearer <token>'")
    return token.strip()


def fingerprint_secret(value: str) -> str:
    """sha256 of a credential, for telling two keys apart without holding one.

    The column this feeds has a check constraint requiring exactly this shape,
    so a raw key cannot be stored there by mistake.
    """
    import hashlib

    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact(value: str | None, *, keep: int = 4) -> str:
    """Render a secret for a log line or a UI without disclosing it."""
    if not value:
        return "<unset>"
    if len(value) <= keep:
        return "*" * len(value)
    return f"{'*' * (len(value) - keep)}{value[-keep:]}"
