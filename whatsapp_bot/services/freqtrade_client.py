"""
Freqtrade REST API client — global singleton.
Connects to the single Freqtrade instance using env vars.
Handles JWT authentication automatically.
"""

from __future__ import annotations

import logging
from typing import Optional

import httpx

from whatsapp_bot.config import settings

logger = logging.getLogger(__name__)


class FreqtradeClient:
    """Async wrapper around the Freqtrade REST API."""

    def __init__(self):
        self.base_url = settings.freqtrade_api_url.rstrip("/")
        self.username = settings.freqtrade_api_username
        self.password = settings.freqtrade_api_password
        self._token: str | None = None

    # ── Auth ───────────────────────────────────────────────

    async def _login(self, client: httpx.AsyncClient) -> str:
        """Authenticate and return JWT token."""
        resp = await client.post(
            f"{self.base_url}/api/v1/token/login",
            auth=(self.username, self.password),
        )
        resp.raise_for_status()
        self._token = resp.json()["access_token"]
        return self._token

    async def _get_headers(self, client: httpx.AsyncClient) -> dict:
        if not self._token:
            await self._login(client)
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        """Make an authenticated request, retrying once on 401."""
        async with httpx.AsyncClient(timeout=30) as client:
            headers = await self._get_headers(client)
            resp = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)

            if resp.status_code == 401:
                self._token = None
                headers = await self._get_headers(client)
                resp = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)

            resp.raise_for_status()
            return resp.json()

    # ── Bot Status ─────────────────────────────────────────

    async def get_status(self) -> list[dict]:
        """Get all open trades with live P&L."""
        return await self._request("GET", "/api/v1/status")

    async def get_profit(self) -> dict:
        """Get overall profit summary."""
        return await self._request("GET", "/api/v1/profit")

    async def get_balance(self) -> dict:
        """Get current portfolio balance."""
        return await self._request("GET", "/api/v1/balance")

    async def get_trades(self, limit: int = 50) -> dict:
        """Get trade history."""
        return await self._request("GET", f"/api/v1/trades?limit={limit}")

    async def get_config(self) -> dict:
        """Get current bot configuration."""
        return await self._request("GET", "/api/v1/show_config")

    # ── Trade Actions ──────────────────────────────────────

    async def force_entry(self, pair: str, side: str = "long", stake_amount: Optional[float] = None) -> dict:
        """Force open a trade."""
        payload = {"pair": pair, "side": side}
        if stake_amount:
            payload["stakeamount"] = stake_amount
        return await self._request("POST", "/api/v1/forcebuy", json=payload)

    async def force_exit(self, trade_id: int) -> dict:
        """Force close a trade by ID."""
        return await self._request("POST", "/api/v1/forcesell", json={"tradeid": trade_id})

    # ── Bot Control ────────────────────────────────────────

    async def start(self) -> dict:
        return await self._request("POST", "/api/v1/start")

    async def stop(self) -> dict:
        return await self._request("POST", "/api/v1/stop")

    async def reload_config(self) -> dict:
        """Reload bot configuration (picks up strategy changes)."""
        return await self._request("POST", "/api/v1/reload_config")

    # ── Health Check ───────────────────────────────────────

    async def ping(self) -> bool:
        """Check if Freqtrade API is reachable."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(f"{self.base_url}/api/v1/ping")
                return resp.status_code == 200
        except Exception:
            return False


# ── Singleton ─────────────────────────────────────────────

_instance: FreqtradeClient | None = None


def get_freqtrade_client() -> FreqtradeClient:
    """Get or create the global FreqtradeClient singleton."""
    global _instance
    if _instance is None:
        _instance = FreqtradeClient()
    return _instance
