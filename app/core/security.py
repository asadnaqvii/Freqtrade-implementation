"""Request authentication.

The API verifies Supabase access tokens itself rather than calling
/auth/v1/user on every request: it is one signature check instead of a network
round trip, and it fails closed.

Supabase signs those tokens one of two ways, and a project mid-migration issues
both:

  asymmetric (current)  ES256/RS256, verified against the project's public JWKS.
                        No shared secret needed; the key is fetched and cached,
                        so rotation works without a redeploy.
  legacy (older)        HS256 against the project's JWT secret.

Routing is by the token's own `alg` header, so a project serving both kinds
works without reconfiguration. That is safe here because the branches never
share key material: HS256 is only ever checked against the configured secret,
and the JWKS public key is only ever used for asymmetric verification. Feeding
a published public key into an HMAC check is exactly the algorithm-confusion
attack this split avoids.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt
from jwt import PyJWKClient, PyJWTError

from app.core.config import ConfigError, get_settings

log = logging.getLogger(__name__)

# Supabase issues tokens with aud="authenticated" for signed-in users.
_EXPECTED_AUDIENCE = "authenticated"

# Asymmetric algorithms Supabase signs access tokens with. HS256 is deliberately
# absent: accepting it on the JWKS path would let a caller present a token signed
# with a public key we just published.
_ASYMMETRIC_ALGORITHMS = ["ES256", "RS256"]


@lru_cache(maxsize=4)
def _jwks_client(url: str) -> PyJWKClient:
    """One JWKS client per project, caching the fetched keys.

    PyJWKClient caches internally, so this is not a network call per request --
    only on a cold start or when a token arrives with an unseen key id, which is
    what makes key rotation work without a redeploy.
    """
    return PyJWKClient(url, cache_keys=True, lifespan=600)


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
    common = {
        "audience": _EXPECTED_AUDIENCE,
        "options": {"require": ["exp", "sub"]},
    }

    try:
        try:
            algorithm = jwt.get_unverified_header(token).get("alg")
        except PyJWTError as exc:
            raise AuthError("malformed token") from exc

        if algorithm == "HS256":
            secret = settings.supabase.jwt_secret
            if not secret:
                # Deny rather than raise a configuration error. An unverifiable
                # token is a 401, not a 500: anyone can send one, and turning
                # that into a server error hands out a trivial way to make the
                # API look broken. The operator hint goes to the log instead.
                log.warning(
                    "an HS256 token arrived but SUPABASE_JWT_SECRET is not set; "
                    "denying. Set it if this project still issues legacy tokens."
                )
                raise AuthError("token could not be verified")
            claims = jwt.decode(token, secret, algorithms=["HS256"], **common)

        elif algorithm in _ASYMMETRIC_ALGORITHMS:
            if not settings.supabase.url:
                raise ConfigError(
                    "SUPABASE_URL is not set, so the project's public keys cannot be "
                    "fetched to verify this token."
                )
            jwks_url = settings.supabase.url.rstrip("/") + "/auth/v1/.well-known/jwks.json"
            signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
            claims = jwt.decode(
                token, signing_key.key, algorithms=_ASYMMETRIC_ALGORITHMS, **common
            )

        else:
            # Notably this refuses alg="none".
            raise AuthError(f"unsupported token algorithm: {algorithm!r}")

    except (ConfigError, AuthError):
        raise
    except PyJWTError as exc:
        # Deliberately vague to the caller; the detail goes to the log only.
        log.info("rejected token: %s", exc)
        raise AuthError("invalid or expired token") from exc
    except Exception as exc:
        # A JWKS fetch failure is ours, not the caller's, but it still must not
        # let the request through.
        log.warning("could not verify token: %s", exc)
        raise AuthError("token could not be verified") from exc

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
