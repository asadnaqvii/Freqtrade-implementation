"""Generic ccxt-backed provider.

One implementation covers every exchange ccxt supports, which is what makes the
platform work with "kucoin or any wallet provider" without a class per venue.
Venue-specific behaviour goes in a subclass only when the generic path gives a
wrong or unhelpful answer -- see kucoin.py.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from app.providers.base import (
    Balance,
    ConnectivityReport,
    Credentials,
    MarketInfo,
    OrderInfo,
    ProviderAuthError,
    ProviderError,
    ProviderGeoBlockError,
    ProviderUnavailableError,
    WalletProvider,
)

log = logging.getLogger(__name__)

# Substrings that mean "we refused you because of where you are", not "your
# request was wrong". Collected across venues because none of them use a
# distinct error code for it.
_GEO_MARKERS = (
    "restricted location",
    "restricted region",
    "unavailable in your country",
    "not available in your region",
    "geographical restriction",
    "geo restriction",
    "ip is not allowed",
    "country is not supported",
    "service unavailable from a restricted location",
    "eligibility",
    "403 forbidden",
    "access denied",
)


def _is_geo_block(message: str) -> bool:
    lowered = message.lower()
    return any(marker in lowered for marker in _GEO_MARKERS)


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class CcxtProvider(WalletProvider):
    """Talks to any ccxt exchange."""

    name = "ccxt"

    def __init__(
        self,
        ccxt_id: str,
        credentials: Credentials | None = None,
        *,
        sandbox: bool = False,
        timeout_ms: int = 20000,
    ) -> None:
        super().__init__(credentials, sandbox=sandbox)
        self.ccxt_id = ccxt_id
        self._timeout_ms = timeout_ms
        self._exchange: Any = None
        self._markets: dict[str, MarketInfo] | None = None

    # -- plumbing ----------------------------------------------------------
    @property
    def exchange(self) -> Any:
        if self._exchange is None:
            self._exchange = self._build()
        return self._exchange

    def _build(self) -> Any:
        import ccxt

        try:
            cls = getattr(ccxt, self.ccxt_id)
        except AttributeError as exc:
            raise ProviderError(
                f"ccxt has no exchange called {self.ccxt_id!r}. "
                f"Pick one of: {', '.join(sorted(ccxt.exchanges)[:12])}, ..."
            ) from exc

        config: dict[str, Any] = {
            "enableRateLimit": True,
            "timeout": self._timeout_ms,
        }
        if self.credentials.key:
            config["apiKey"] = self.credentials.key
        if self.credentials.secret:
            config["secret"] = self.credentials.secret
        if self.credentials.password:
            config["password"] = self.credentials.password

        exchange = cls(config)
        if self.sandbox:
            try:
                exchange.set_sandbox_mode(True)
            except Exception as exc:  # pragma: no cover - venue dependent
                raise ProviderError(
                    f"{self.ccxt_id} does not offer a sandbox: {exc}"
                ) from exc
        return exchange

    def _translate(self, exc: Exception) -> ProviderError:
        """Map a ccxt exception onto the interface's error vocabulary."""
        import ccxt

        message = str(exc)

        if _is_geo_block(message):
            return ProviderGeoBlockError(
                f"{self.ccxt_id} refused the request from this location",
                venue=self.ccxt_id,
                detail=message[:500],
            )
        if isinstance(exc, ccxt.AuthenticationError):
            return ProviderAuthError(f"{self.ccxt_id} rejected the credentials: {message[:300]}")
        if isinstance(exc, ccxt.PermissionDenied):
            # Permission denied and geo-blocking share a status code on several
            # venues; only the body distinguishes them, and we checked above.
            return ProviderAuthError(f"{self.ccxt_id} denied permission: {message[:300]}")
        if isinstance(exc, (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout)):
            return ProviderUnavailableError(f"{self.ccxt_id} unreachable: {message[:300]}")
        return ProviderError(f"{self.ccxt_id} error: {message[:300]}")

    def close(self) -> None:
        exchange = self._exchange
        self._exchange = None
        if exchange is not None:
            closer = getattr(exchange, "close", None)
            if callable(closer):
                try:
                    closer()
                except Exception:  # pragma: no cover - best effort
                    pass

    # -- interface ---------------------------------------------------------
    def check_connectivity(self) -> ConnectivityReport:
        started = time.perf_counter()
        egress_ip, egress_country = _egress_identity()

        try:
            server_ms = self.exchange.fetch_time()
            latency = (time.perf_counter() - started) * 1000
            skew = (server_ms / 1000.0) - time.time() if server_ms else None
            return ConnectivityReport(
                reachable=True,
                geo_blocked=False,
                latency_ms=round(latency, 1),
                server_time_skew_seconds=round(skew, 3) if skew is not None else None,
                egress_ip=egress_ip,
                egress_country=egress_country,
            )
        except Exception as exc:
            translated = self._translate(exc)
            latency = (time.perf_counter() - started) * 1000
            geo = isinstance(translated, ProviderGeoBlockError)
            return ConnectivityReport(
                reachable=False,
                geo_blocked=geo,
                latency_ms=round(latency, 1),
                server_time_skew_seconds=None,
                egress_ip=egress_ip,
                egress_country=egress_country,
                detail=str(translated),
            )

    def verify_credentials(self) -> dict[str, Any]:
        if not self.credentials.present:
            raise ProviderAuthError("no API key and secret were resolved for this account")
        try:
            balance = self.exchange.fetch_balance()
        except Exception as exc:
            raise self._translate(exc) from exc
        return {"currencies_held": len([c for c, v in (balance.get("total") or {}).items() if v])}

    def permissions(self) -> set[str]:
        """Refuse to guess. ccxt has no portable permissions call.

        The tempting implementation infers from what succeeds: fetch a balance,
        conclude "read". But the check that consumes this exists to catch a key
        that can *withdraw*, and inference cannot see withdrawal rights without
        attempting a withdrawal. Returning an inferred set would therefore report
        "no withdrawal rights" for every venue we cannot introspect -- a security
        control that silently passes is worse than one that says it did not run.

        Subclasses that can ask the venue directly override this.
        """
        try:
            self.exchange.fetch_balance()
        except Exception as exc:
            # Surface a real failure -- unreachable, geo-blocked, bad key --
            # rather than reporting it as an unsupported introspection.
            raise self._translate(exc) from exc
        raise ProviderError(
            f"{self.ccxt_id} does not report what an API key is allowed to do, so "
            "this cannot be checked automatically. Confirm on the exchange that the "
            "key can trade and cannot withdraw."
        )

    def fetch_balances(self) -> list[Balance]:
        try:
            raw = self.exchange.fetch_balance()
        except Exception as exc:
            raise self._translate(exc) from exc

        totals = raw.get("total") or {}
        frees = raw.get("free") or {}
        useds = raw.get("used") or {}

        balances = []
        for currency, total in totals.items():
            total_f = _f(total) or 0.0
            free_f = _f(frees.get(currency)) or 0.0
            used_f = _f(useds.get(currency)) or 0.0
            if total_f == 0 and free_f == 0 and used_f == 0:
                continue
            balances.append(Balance(currency=currency, free=free_f, used=used_f, total=total_f))
        return sorted(balances, key=lambda b: -b.total)

    def earliest_candle(self, symbol: str, timeframe: str = "1d") -> datetime | None:
        """When this venue's history for a pair begins.

        Two things make this less trivial than it looks. Markets have to be
        loaded first or ccxt cannot resolve the symbol at all. And `since=0` is
        not portable: some venues answer it with their oldest candle, others
        answer with nothing, which is indistinguishable from "no such pair".

        So: ask for the beginning of time, and if that comes back empty, binary
        search for the boundary. About fifteen requests, and it gives a real
        answer on any venue rather than a guess that happens to work on one.
        """
        try:
            self.exchange.load_markets()
        except Exception as exc:
            raise self._translate(exc) from exc

        markets = getattr(self.exchange, "markets", None) or {}
        if symbol not in markets:
            raise ProviderError(f"{self.ccxt_id} does not list {symbol}")

        def candles_from(since_ms: int) -> list:
            try:
                return self.exchange.fetch_ohlcv(symbol, timeframe, since=since_ms, limit=1) or []
            except Exception as exc:
                raise self._translate(exc) from exc

        def to_dt(rows: list) -> datetime | None:
            try:
                return datetime.fromtimestamp(rows[0][0] / 1000, tz=timezone.utc)
            except (TypeError, ValueError, IndexError, OSError):
                return None

        # 2010: comfortably before any crypto exchange this code will meet.
        floor_ms = int(datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        rows = candles_from(floor_ms)
        if rows:
            return to_dt(rows)

        now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        if not candles_from(now_ms - 7 * 86400_000):
            # Nothing recent either: the venue has no history for this pair at
            # this timeframe, which is a real answer and not a failure.
            return None

        lo, hi = floor_ms, now_ms
        while hi - lo > 86400_000:          # stop once the gap is under a day
            mid = (lo + hi) // 2
            if candles_from(mid):
                hi = mid
            else:
                lo = mid
        return to_dt(candles_from(hi))

    def fetch_markets(self) -> dict[str, MarketInfo]:
        if self._markets is not None:
            return self._markets
        try:
            raw = self.exchange.load_markets()
        except Exception as exc:
            raise self._translate(exc) from exc

        markets: dict[str, MarketInfo] = {}
        for symbol, m in raw.items():
            limits = m.get("limits") or {}
            amount_limits = limits.get("amount") or {}
            cost_limits = limits.get("cost") or {}
            price_limits = limits.get("price") or {}
            precision = m.get("precision") or {}
            markets[symbol] = MarketInfo(
                symbol=symbol,
                base=m.get("base") or "",
                quote=m.get("quote") or "",
                active=bool(m.get("active", True)),
                spot=bool(m.get("spot", True)),
                min_amount=_f(amount_limits.get("min")),
                max_amount=_f(amount_limits.get("max")),
                min_cost=_f(cost_limits.get("min")),
                min_price=_f(price_limits.get("min")),
                amount_precision=_f(precision.get("amount")),
                price_precision=_f(precision.get("price")),
                maker_fee=_f(m.get("maker")),
                taker_fee=_f(m.get("taker")),
            )
        self._markets = markets
        return markets

    def fetch_orders(
        self, symbol: str, *, since: datetime | None = None, limit: int = 100
    ) -> list[OrderInfo]:
        since_ms = int(since.timestamp() * 1000) if since else None
        try:
            if self.exchange.has.get("fetchOrders"):
                raw = self.exchange.fetch_orders(symbol, since=since_ms, limit=limit)
            elif self.exchange.has.get("fetchClosedOrders"):
                # KuCoin among others only exposes open and closed separately.
                raw = self.exchange.fetch_closed_orders(symbol, since=since_ms, limit=limit)
                raw += self.exchange.fetch_open_orders(symbol)
            else:
                raise ProviderError(
                    f"{self.ccxt_id} cannot list historical orders through ccxt"
                )
        except ProviderError:
            raise
        except Exception as exc:
            raise self._translate(exc) from exc

        return [_to_order(o) for o in raw]

    def fetch_order(self, order_id: str, symbol: str) -> OrderInfo | None:
        # Imported here like everywhere else in this module: ccxt is a heavy
        # dependency the app can start without.
        import ccxt

        if not self.exchange.has.get("fetchOrder"):
            raise ProviderError(f"{self.ccxt_id} cannot look up a single order through ccxt")
        try:
            return _to_order(self.exchange.fetch_order(order_id, symbol))
        except ccxt.OrderNotFound:
            # An answer, not a failure: it is exactly the finding reconciliation
            # is trying to establish.
            return None
        except Exception as exc:  # noqa: BLE001
            raise self._translate(exc) from exc

    def describe(self) -> dict[str, Any]:
        base = super().describe()
        base.update({"ccxt_id": self.ccxt_id})
        return base


def _to_order(raw: dict[str, Any]) -> OrderInfo:
    fee = raw.get("fee") or {}
    ts = raw.get("timestamp")
    return OrderInfo(
        order_id=str(raw.get("id") or ""),
        symbol=raw.get("symbol") or "",
        side=raw.get("side"),
        status=raw.get("status"),
        order_type=raw.get("type"),
        price=_f(raw.get("price")),
        average=_f(raw.get("average")),
        amount=_f(raw.get("amount")),
        filled=_f(raw.get("filled")),
        remaining=_f(raw.get("remaining")),
        cost=_f(raw.get("cost")),
        fee_cost=_f(fee.get("cost")),
        fee_currency=fee.get("currency"),
        timestamp=datetime.fromtimestamp(ts / 1000, tz=timezone.utc) if ts else None,
        raw=raw,
    )


def _egress_identity() -> tuple[str | None, str | None]:
    """Which IP and country this process appears to come from.

    Recorded alongside every connectivity result because a KuCoin verdict is
    only interpretable next to the region it was measured from -- the same keys
    and the same code pass from Singapore and fail from Oregon.
    """
    import httpx

    try:
        with httpx.Client(timeout=5.0) as client:
            response = client.get("https://ipinfo.io/json")
            if response.status_code == 200:
                data = response.json()
                return data.get("ip"), data.get("country")
    except Exception:
        pass
    return None, None
