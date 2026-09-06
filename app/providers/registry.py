"""Resolve an exchange_accounts row into a live provider."""

from __future__ import annotations

from typing import Any

from app.providers import credentials as creds
from app.providers.base import Credentials, ProviderError, WalletProvider
from app.providers.ccxt_provider import CcxtProvider
from app.providers.kucoin import KuCoinProvider
from app.providers.paper import PaperProvider

# Venues that need behaviour the generic ccxt path gets wrong.
_SPECIALISED: dict[str, type[WalletProvider]] = {
    "kucoin": KuCoinProvider,
    "paper": PaperProvider,
}


def build(account: dict[str, Any] | None = None, *, credentials: Credentials | None = None) -> WalletProvider:
    """Build the provider for an account row.

    Any venue ccxt supports works without being listed here; `_SPECIALISED` is
    only for the ones where the generic answer would be wrong or unhelpful.
    """
    account = account or {}
    provider = (account.get("provider") or "kucoin").lower()
    sandbox = bool(account.get("is_sandbox"))
    resolved = credentials if credentials is not None else creds.resolve(account)

    if provider in _SPECIALISED:
        cls = _SPECIALISED[provider]
        if cls is PaperProvider:
            return PaperProvider(resolved, sandbox=sandbox)
        return cls(resolved, sandbox=sandbox)  # type: ignore[call-arg]

    ccxt_id = account.get("ccxt_id") or provider
    if not ccxt_id:
        raise ProviderError(
            f"account {account.get('label', '<unnamed>')!r} has provider {provider!r} "
            "but no ccxt_id, so there is no driver to talk to it"
        )
    return CcxtProvider(ccxt_id, resolved, sandbox=sandbox)


def available() -> list[str]:
    """Every venue that can be connected, for the UI's picker."""
    try:
        import ccxt

        return sorted(set(ccxt.exchanges) | set(_SPECIALISED))
    except ImportError:  # pragma: no cover
        return sorted(_SPECIALISED)
