"""A provider that answers plausibly without touching a venue.

Exists so the verification flow, the UI and the tests can all be exercised with
no credentials and no network. It reports itself as not live, and the validation
engine marks any run against it accordingly -- a paper pass must never be
mistaken for evidence that real keys work.
"""

from __future__ import annotations

from datetime import datetime, timezone

from app.providers.base import (
    Balance,
    ConnectivityReport,
    Credentials,
    MarketInfo,
    OrderInfo,
    WalletProvider,
)

_MARKETS = {
    "BTC/USDT": ("BTC", "USDT", 0.00001, 1.0),
    "ETH/USDT": ("ETH", "USDT", 0.0001, 1.0),
    "SOL/USDT": ("SOL", "USDT", 0.001, 1.0),
    "ADA/USDT": ("ADA", "USDT", 0.1, 1.0),
}


class PaperProvider(WalletProvider):
    name = "paper"
    is_live = False

    def __init__(
        self,
        credentials: Credentials | None = None,
        *,
        sandbox: bool = False,
        balance: float = 1000.0,
    ) -> None:
        super().__init__(credentials, sandbox=sandbox)
        self.balance = balance

    def check_connectivity(self) -> ConnectivityReport:
        return ConnectivityReport(
            reachable=True,
            geo_blocked=False,
            latency_ms=0.0,
            server_time_skew_seconds=0.0,
            egress_ip=None,
            egress_country=None,
            detail="simulated provider; no network call was made",
        )

    def verify_credentials(self) -> dict:
        return {"simulated": True}

    def permissions(self) -> set[str]:
        return {"read", "trade"}

    def fetch_balances(self) -> list[Balance]:
        return [Balance(currency="USDT", free=self.balance, used=0.0, total=self.balance)]

    def fetch_markets(self) -> dict[str, MarketInfo]:
        return {
            symbol: MarketInfo(
                symbol=symbol, base=base, quote=quote, active=True, spot=True,
                min_amount=min_amount, min_cost=min_cost,
                maker_fee=0.001, taker_fee=0.001,
            )
            for symbol, (base, quote, min_amount, min_cost) in _MARKETS.items()
        }

    def fetch_orders(
        self, symbol: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[OrderInfo]:
        return []
