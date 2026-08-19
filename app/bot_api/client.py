"""A deliberately small client for the bot's freqtrade REST API.

The bot is a Render private service: no public URL, reachable only from inside
the region. This client is how the dashboard shows live positions, profit and
balances, and how a force-exit gets placed.

That means the app service holds the bot's API login, which is a real widening
of what a compromise of the public surface could do -- freqtrade's API can close
positions. Two things bound it:

  * Every path this client may touch is listed below. Anything not in READS or
    ACTIONS raises before a request is made, so "the app can talk to the bot"
    cannot quietly grow into "the app can do anything to the bot". Adding a
    capability means adding it here, in a diff, on purpose.

  * ACTIONS is separate from READS and holds exactly one entry. Reading is
    routine; acting is not, and the split keeps that visible.

Notably absent: /forceenter. Closing a position you already hold is a control a
dashboard should offer; opening a new one from a web page is a different kind of
power, and the strategy is what decides entries.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.core.config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(25.0, connect=5.0)


class BotError(RuntimeError):
    """The bot answered, but not with what was asked for."""


class BotUnreachable(BotError):
    """The bot did not answer at all. Distinct: usually a deploy in progress."""


class BotNotConfigured(BotError):
    """This service has no bot address or login."""


class BotClient:
    #: Read-only endpoints. Safe to call on any page load.
    READS = {
        "ping",            # liveness, no auth
        "show_config",     # dry-run flag, stake, timeframe, strategy
        "status",          # open trades, with live profit
        "profit",          # aggregate P&L summary
        "daily",           # per-day profit, for the chart
        "balance",         # per-currency wallet
        "count",           # open vs max trades
        "performance",     # profit by pair
        "entries",         # profit by entry tag
        "exits",           # profit by exit reason
        "mix_tags",        # entry+exit tag combinations
        "stats",           # exit reason counts and trade durations
        "trades",          # closed trade history
        "whitelist",       # pairs currently tradable
        "blacklist",       # pairs excluded
        "locks",           # pairs locked after a loss
        "logs",            # recent bot log lines
        "sysinfo",         # cpu/ram of the bot process
        "version",
        "health",
    }

    #: State-changing endpoints. One entry, on purpose -- see the module docstring.
    ACTIONS = {"forceexit"}

    def __init__(self, base_url: str, username: str | None, password: str | None) -> None:
        self.base_url = base_url.rstrip("/")
        self._auth = (username, password) if username and password else None

    @classmethod
    def from_settings(cls) -> "BotClient":
        settings = get_settings()
        if not settings.bot.api_base_url:
            raise BotNotConfigured(
                "FREQTRADE_API_BASE_URL is not set, so this service does not know "
                "where the bot is."
            )
        return cls(
            settings.bot.api_base_url,
            settings.bot.api_username,
            settings.bot.api_password,
        )

    # -- plumbing ----------------------------------------------------------
    def _call(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}/api/v1/{path}"
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                response = client.request(method, url, auth=self._auth, **kwargs)
        except httpx.HTTPError as exc:
            raise BotUnreachable(
                f"the bot did not answer at {self.base_url}. It may be restarting; "
                f"try again shortly. ({exc})"
            ) from exc

        if response.status_code in (401, 403):
            raise BotError(
                "the bot rejected this request. API_USERNAME and API_PASSWORD must "
                "be identical on the app and bot services."
            )
        if response.status_code >= 400:
            raise BotError(f"the bot returned {response.status_code}: {response.text[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise BotError(f"the bot sent a non-JSON reply for {path}") from exc

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if path not in self.READS:
            raise BotError(f"{path!r} is not a permitted read")
        return self._call("GET", path, params=params or {})

    def act(self, path: str, body: dict[str, Any] | None = None) -> Any:
        if path not in self.ACTIONS:
            raise BotError(f"{path!r} is not a permitted action")
        log.warning("bot action %s %s", path, body or {})
        return self._call("POST", path, json=body or {})

    # -- the questions the dashboard asks ----------------------------------
    def overview(self) -> dict[str, Any]:
        """Everything the live page needs, in one round trip from the browser.

        Each part is fetched independently and a failure is recorded rather than
        raised: a bot that cannot reach the exchange should still be able to tell
        you its open positions, and one slow endpoint should not blank the page.
        """
        out: dict[str, Any] = {"errors": {}}
        wanted = {
            "config": "show_config",
            "status": "status",
            "profit": "profit",
            "daily": "daily",
            "balance": "balance",
            "count": "count",
            "performance": "performance",
        }
        for key, path in wanted.items():
            try:
                out[key] = self.get(path)
            except BotError as exc:
                out[key] = None
                out["errors"][key] = str(exc)
        return out

    def force_exit(self, trade_id: str, *, order_type: str | None = None,
                   amount: float | None = None) -> Any:
        body: dict[str, Any] = {"tradeid": str(trade_id)}
        if order_type:
            body["ordertype"] = order_type
        if amount is not None:
            body["amount"] = amount
        return self.act("forceexit", body)
