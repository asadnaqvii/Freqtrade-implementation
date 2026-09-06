"""Where a provider's credentials come from.

This is the seam the whole single-user-now, multi-user-later plan hangs on.
`exchange_accounts` stores the NAME of the environment variable holding a key,
never the key itself -- there is a check constraint in the database enforcing
that the column looks like an env var name, so a secret cannot be parked there
by accident.

Today resolution reads the process environment, which is exactly right for one
operator with keys in Render's env. Supporting many users later means changing
`resolve` to decrypt from a credential store; nothing that calls it changes.
"""

from __future__ import annotations

import logging
import os
import re

from app.providers.base import Credentials

log = logging.getLogger(__name__)

ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")

# Defaults matching the env vars this deployment already sets.
DEFAULT_KEY_VAR = "FREQTRADE__EXCHANGE__KEY"
DEFAULT_SECRET_VAR = "FREQTRADE__EXCHANGE__SECRET"
DEFAULT_PASSWORD_VAR = "FREQTRADE__EXCHANGE__PASSWORD"


class CredentialError(RuntimeError):
    pass


def _read(var_name: str | None, *, fallback: str | None = None) -> str | None:
    name = var_name or fallback
    if not name:
        return None
    if not ENV_VAR_RE.match(name):
        # A value that is not env-var-shaped is almost certainly a pasted secret.
        # Refuse rather than treat it as one, and say so without echoing it.
        raise CredentialError(
            f"{name[:8]}... is not a valid environment variable name. "
            "This column stores the NAME of the variable holding the key, not the key."
        )
    return os.environ.get(name) or None


def resolve(account: dict | None = None) -> Credentials:
    """Resolve credentials for an exchange_accounts row.

    Passing None resolves the deployment-wide credentials, which is what the bot
    itself uses.
    """
    account = account or {}
    key = _read(account.get("api_key_env_var"), fallback=DEFAULT_KEY_VAR)
    secret = _read(account.get("api_secret_env_var"), fallback=DEFAULT_SECRET_VAR)
    password = _read(account.get("api_password_env_var"), fallback=DEFAULT_PASSWORD_VAR)

    if not key or not secret:
        missing = account.get("api_key_env_var") or DEFAULT_KEY_VAR
        log.info("no credentials resolved for account %s (expected %s)",
                 account.get("label", "<default>"), missing)

    return Credentials(key=key, secret=secret, password=password)


def fingerprint(credentials: Credentials) -> str | None:
    """sha256 of the key, for recognising it later without storing it."""
    if not credentials.key:
        return None
    import hashlib

    return hashlib.sha256(credentials.key.encode("utf-8")).hexdigest()
