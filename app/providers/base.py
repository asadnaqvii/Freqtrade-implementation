"""The wallet provider interface.

"Verification against the user's own wallet" means the platform holds no funds
and no house account: every check runs with the user's own credentials against
the venue they actually trade on. This module defines what a venue has to be
able to answer for that to work.

CcxtProvider implements this for any of the ~100 exchanges ccxt supports, which
is what makes "kucoin or any wallet provider" true in practice rather than in
principle. KuCoinProvider subclasses it for the quirks that matter.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class ProviderError(RuntimeError):
    """Base for anything that went wrong talking to a venue."""


class ProviderAuthError(ProviderError):
    """Credentials were rejected."""


class ProviderGeoBlockError(ProviderError):
    """The venue refused this request because of where it came from.

    Split out from a generic connectivity failure because the remedy is
    completely different: nothing about the keys or the code is wrong, the
    request just left from the wrong country.
    """

    def __init__(self, message: str, *, venue: str, detail: str | None = None) -> None:
        super().__init__(message)
        self.venue = venue
        self.detail = detail


class ProviderUnavailableError(ProviderError):
    """The venue could not be reached, or answered with a server error."""


@dataclass(frozen=True)
class Credentials:
    """What a venue needs to authenticate a request.

    Resolved from the environment at the point of use and never persisted.
    """

    key: str | None = None
    secret: str | None = None
    password: str | None = None

    @property
    def present(self) -> bool:
        return bool(self.key and self.secret)

    def __repr__(self) -> str:  # pragma: no cover - defensive
        # A stray repr() in a log line must not leak a key.
        return (
            f"Credentials(key={'set' if self.key else 'unset'}, "
            f"secret={'set' if self.secret else 'unset'}, "
            f"password={'set' if self.password else 'unset'})"
        )


@dataclass(frozen=True)
class Balance:
    currency: str
    free: float
    used: float
    total: float


@dataclass(frozen=True)
class MarketInfo:
    """Everything needed to decide whether an order would be accepted."""

    symbol: str
    base: str
    quote: str
    active: bool
    spot: bool
    min_amount: float | None = None
    max_amount: float | None = None
    min_cost: float | None = None
    min_price: float | None = None
    amount_precision: float | None = None
    price_precision: float | None = None
    maker_fee: float | None = None
    taker_fee: float | None = None


@dataclass(frozen=True)
class OrderInfo:
    """One order as the venue reports it, for reconciliation against the bot."""

    order_id: str
    symbol: str
    side: str | None
    status: str | None
    order_type: str | None
    price: float | None
    average: float | None
    amount: float | None
    filled: float | None
    remaining: float | None
    cost: float | None
    fee_cost: float | None
    fee_currency: str | None
    timestamp: datetime | None
    raw: dict[str, Any] = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class ConnectivityReport:
    reachable: bool
    geo_blocked: bool
    latency_ms: float | None
    server_time_skew_seconds: float | None
    egress_ip: str | None
    egress_country: str | None
    detail: str | None = None


class WalletProvider(abc.ABC):
    """A venue the platform can verify a user's account against.

    Implementations must not raise for an ordinary "no" -- an inactive market, a
    zero balance -- but must raise ProviderError subclasses for anything that
    means the answer is unknown. The validation engine distinguishes "checked and
    failed" from "could not check", and it can only do that if this line holds.
    """

    #: Stable key used in exchange_accounts.provider.
    name: str = "abstract"

    #: Whether this provider talks to a real venue at all.
    is_live: bool = True

    def __init__(self, credentials: Credentials | None = None, *, sandbox: bool = False) -> None:
        self.credentials = credentials or Credentials()
        self.sandbox = sandbox

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        """Release any network resources. Safe to call more than once."""

    def __enter__(self) -> "WalletProvider":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- required ----------------------------------------------------------
    @abc.abstractmethod
    def check_connectivity(self) -> ConnectivityReport:
        """Reach the venue's public API. Must not require credentials."""

    @abc.abstractmethod
    def verify_credentials(self) -> dict[str, Any]:
        """Make one authenticated call. Raise ProviderAuthError if rejected."""

    @abc.abstractmethod
    def permissions(self) -> set[str]:
        """Permissions the key carries, lowercased.

        'withdraw' appearing here is a finding, not a feature: a trading bot has
        no use for withdrawal rights, and a key that has them turns a bot
        compromise into a funds loss.
        """

    @abc.abstractmethod
    def fetch_balances(self) -> list[Balance]:
        ...

    @abc.abstractmethod
    def fetch_markets(self) -> dict[str, MarketInfo]:
        ...

    @abc.abstractmethod
    def fetch_orders(self, symbol: str, *, since: datetime | None = None, limit: int = 100) -> list[OrderInfo]:
        ...

    def fetch_order(self, order_id: str, symbol: str) -> OrderInfo | None:
        """One order by its venue id, or None if the venue does not know it.

        Optional, and separate from fetch_orders for a reason: bulk listings are
        windowed differently at every venue -- KuCoin does not implement
        fetchOrders at all and its closed-order listing is time-limited -- so
        "not in the list" is not the same question as "the venue has no such
        order". Reconciliation needs the second one before it accuses a bot of
        inventing a trade.
        """
        raise ProviderError(f"{self.name} cannot look up a single order")

    # -- optional ----------------------------------------------------------
    def earliest_candle(self, symbol: str, timeframe: str = "1d") -> datetime | None:
        """When this venue's history for a pair begins, or None if unknown.

        Optional because not every provider has candles at all; the default says
        so rather than inventing a date.
        """
        raise ProviderError(
            f"{self.name} cannot report how far back its candle history goes"
        )

    def describe(self) -> dict[str, Any]:
        return {"provider": self.name, "sandbox": self.sandbox, "live": self.is_live}
