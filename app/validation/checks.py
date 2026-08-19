"""Individual verification checks.

Each check answers one question about a user's own wallet and returns a
CheckResult rather than raising, so one failure never hides the rest -- the point
of a verification run is a complete picture, not the first problem.

Every failure carries a remediation string. A check that says "failed" and
nothing else just moves the debugging to the user.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.providers.base import (
    ConnectivityReport,
    ProviderAuthError,
    ProviderError,
    ProviderGeoBlockError,
    ProviderUnavailableError,
    WalletProvider,
)
from app.providers.kucoin import CLOCK_SKEW_LIMIT_SECONDS, GEO_REMEDY

log = logging.getLogger(__name__)

PASSED, WARNING, FAILED, SKIPPED, ERROR = "passed", "warning", "failed", "skipped", "error"
INFO, WARN_SEV, CRITICAL = "info", "warning", "critical"

# Countries KuCoin and several other venues refuse. Used to explain a geo-block
# before the venue is even asked, when we can see where we are.
BLOCKED_EGRESS = {"US"}


@dataclass
class CheckResult:
    code: str
    title: str
    status: str
    severity: str = WARN_SEV
    message: str = ""
    expected: Any = None
    actual: Any = None
    remediation: str | None = None
    pair: str | None = None
    duration_ms: int | None = None

    @property
    def is_bad(self) -> bool:
        return self.status in (FAILED, ERROR)

    def as_row(self, run_id: str) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "code": self.code,
            "title": self.title,
            "status": self.status,
            "severity": self.severity,
            "pair": self.pair,
            "message": self.message or None,
            "expected": self.expected,
            "actual": self.actual,
            "remediation": self.remediation,
            "duration_ms": self.duration_ms,
        }


@dataclass
class CheckContext:
    """Everything the checks need that is not the provider itself."""

    provider: WalletProvider
    pairs: list[str] = field(default_factory=list)
    stake_currency: str = "USDT"
    stake_amount: float = 10.0
    max_open_trades: int = 1
    connectivity: ConnectivityReport | None = None


def timed(fn: Callable[..., CheckResult]) -> Callable[..., CheckResult]:
    """Record how long a check took; useful for spotting a slow venue."""

    def wrapper(*args: Any, **kwargs: Any) -> CheckResult:
        started = time.perf_counter()
        try:
            result = fn(*args, **kwargs)
        except Exception as exc:  # a check must never take down the run
            log.exception("check %s blew up", fn.__name__)
            result = CheckResult(
                code=f"internal.{fn.__name__}",
                title=fn.__name__,
                status=ERROR,
                severity=CRITICAL,
                message=f"{type(exc).__name__}: {exc}",
                remediation="This is a bug in the platform, not in your account.",
            )
        result.duration_ms = int((time.perf_counter() - started) * 1000)
        return result

    return wrapper


# ---------------------------------------------------------------------------
# Connectivity
# ---------------------------------------------------------------------------

@timed
def check_reachable(ctx: CheckContext) -> CheckResult:
    report = ctx.provider.check_connectivity()
    ctx.connectivity = report

    if report.reachable:
        return CheckResult(
            code="provider.reachable",
            title="Exchange reachable",
            status=PASSED,
            severity=INFO,
            message=f"Answered in {report.latency_ms} ms from {report.egress_country or 'unknown'}.",
            actual={"latency_ms": report.latency_ms, "egress_country": report.egress_country},
        )

    if report.geo_blocked:
        return CheckResult(
            code="provider.geo_block",
            title="Exchange refused this location",
            status=FAILED,
            severity=CRITICAL,
            message=(
                f"The venue refused a request from {report.egress_country or 'this host'}"
                f" ({report.egress_ip or 'unknown IP'})."
            ),
            expected={"egress_country": "any non-restricted region"},
            actual={"egress_country": report.egress_country, "egress_ip": report.egress_ip},
            remediation=report.detail or GEO_REMEDY,
        )

    return CheckResult(
        code="provider.reachable",
        title="Exchange reachable",
        status=FAILED,
        severity=CRITICAL,
        message=report.detail or "Could not reach the venue.",
        remediation=(
            "Check outbound network access from this host, then the venue's status page. "
            "If this host is behind a proxy, confirm the proxy allows the venue."
        ),
    )


@timed
def check_egress_region(ctx: CheckContext) -> CheckResult:
    report = ctx.connectivity
    if report is None or not report.egress_country:
        return CheckResult(
            code="provider.egress_region",
            title="Egress region",
            status=SKIPPED,
            severity=INFO,
            message="Could not determine which country this host egresses from.",
        )

    country = report.egress_country.upper()
    if country in BLOCKED_EGRESS:
        return CheckResult(
            code="provider.egress_region",
            title="Egress region",
            status=FAILED,
            severity=CRITICAL,
            message=f"This host egresses from {country}, which KuCoin and several other venues block.",
            expected={"not_in": sorted(BLOCKED_EGRESS)},
            actual={"egress_country": country, "egress_ip": report.egress_ip},
            remediation=GEO_REMEDY,
        )

    return CheckResult(
        code="provider.egress_region",
        title="Egress region",
        status=PASSED,
        severity=INFO,
        message=f"Egressing from {country}.",
        actual={"egress_country": country},
    )


@timed
def check_clock_skew(ctx: CheckContext) -> CheckResult:
    report = ctx.connectivity
    skew = report.server_time_skew_seconds if report else None
    if skew is None:
        return CheckResult(
            code="provider.clock_skew",
            title="Clock synchronisation",
            status=SKIPPED,
            severity=INFO,
            message="Venue did not report its server time.",
        )

    if abs(skew) > CLOCK_SKEW_LIMIT_SECONDS:
        return CheckResult(
            code="provider.clock_skew",
            title="Clock synchronisation",
            status=FAILED,
            severity=CRITICAL,
            message=f"This host's clock is {skew:+.1f}s from the venue's.",
            expected={"max_abs_skew_seconds": CLOCK_SKEW_LIMIT_SECONDS},
            actual={"skew_seconds": skew},
            remediation=(
                "Signed requests will be rejected. Enable NTP on the host. On a "
                "container platform this usually means the host clock, not the container."
            ),
        )

    return CheckResult(
        code="provider.clock_skew",
        title="Clock synchronisation",
        status=PASSED,
        severity=INFO,
        message=f"Clock is within {abs(skew):.2f}s of the venue.",
        actual={"skew_seconds": skew},
    )


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

@timed
def check_credentials(ctx: CheckContext) -> CheckResult:
    if not ctx.provider.is_live:
        # A simulated provider has no credentials by design. Failing it here
        # would train people to ignore a red credentials check.
        return CheckResult(
            code="provider.credentials",
            title="API credentials",
            status=SKIPPED,
            severity=INFO,
            message="Simulated provider; there are no credentials to verify.",
        )

    if not ctx.provider.credentials.present:
        return CheckResult(
            code="provider.credentials",
            title="API credentials",
            status=FAILED,
            severity=CRITICAL,
            message="No API key and secret were resolved for this account.",
            remediation=(
                "exchange_accounts stores the NAME of the environment variable holding "
                "each secret. Confirm those variables are set on this service."
            ),
        )

    try:
        detail = ctx.provider.verify_credentials()
    except ProviderGeoBlockError as exc:
        return CheckResult(
            code="provider.credentials", title="API credentials", status=SKIPPED,
            severity=WARN_SEV,
            message="Could not test the credentials because the venue blocked this location.",
            remediation=exc.detail or GEO_REMEDY,
        )
    except ProviderAuthError as exc:
        return CheckResult(
            code="provider.credentials", title="API credentials", status=FAILED,
            severity=CRITICAL, message=str(exc),
            remediation=(
                "Regenerate the key on the venue and update the environment variables. "
                "Check the passphrase too if the venue uses one."
            ),
        )
    except ProviderUnavailableError as exc:
        return CheckResult(
            code="provider.credentials", title="API credentials", status=SKIPPED,
            severity=WARN_SEV, message=f"Venue unavailable: {exc}",
        )
    except ProviderError as exc:
        return CheckResult(
            code="provider.credentials", title="API credentials", status=ERROR,
            severity=CRITICAL, message=str(exc),
        )

    return CheckResult(
        code="provider.credentials", title="API credentials", status=PASSED,
        severity=INFO, message="The venue accepted an authenticated request.",
        actual=detail,
    )


@timed
def check_permissions(ctx: CheckContext) -> CheckResult:
    """A trading key must be able to trade, and must not be able to withdraw.

    Withdrawal rights on a bot key are the difference between a compromise that
    costs you a strategy and one that costs you the balance.
    """
    try:
        permissions = {p.lower() for p in ctx.provider.permissions()}
    except ProviderGeoBlockError:
        return CheckResult(
            code="provider.permissions", title="Key permissions", status=SKIPPED,
            severity=WARN_SEV, message="Blocked before permissions could be read.",
        )
    except ProviderError as exc:
        return CheckResult(
            code="provider.permissions", title="Key permissions", status=SKIPPED,
            severity=WARN_SEV, message=f"Could not read permissions: {exc}",
        )

    withdraw = {p for p in permissions if "withdraw" in p or "transfer" in p}
    if withdraw:
        return CheckResult(
            code="provider.permissions", title="Key permissions", status=FAILED,
            severity=CRITICAL,
            message=f"This key can move funds off the exchange ({', '.join(sorted(withdraw))}).",
            expected={"forbidden": ["withdraw", "transfer"]},
            actual={"permissions": sorted(permissions)},
            remediation=(
                "Create a new key with trade and read rights only, and disable withdrawal. "
                "A trading bot never needs to withdraw, so this permission adds risk and "
                "no capability. Consider binding the key to this host's IP as well."
            ),
        )

    can_trade = any("trade" in p or "spot" in p for p in permissions)
    if not can_trade:
        return CheckResult(
            code="provider.permissions", title="Key permissions", status=WARNING,
            severity=WARN_SEV,
            message=f"No trade permission detected (saw: {', '.join(sorted(permissions)) or 'none'}).",
            actual={"permissions": sorted(permissions)},
            remediation=(
                "If this key is meant to place orders, enable trading on it. "
                "Read-only is fine for a monitoring-only account."
            ),
        )

    return CheckResult(
        code="provider.permissions", title="Key permissions", status=PASSED,
        severity=INFO,
        message=f"Trade rights present, no withdrawal rights ({', '.join(sorted(permissions))}).",
        actual={"permissions": sorted(permissions)},
    )


# ---------------------------------------------------------------------------
# Markets and balance
# ---------------------------------------------------------------------------

@timed
def check_pairs_tradable(ctx: CheckContext) -> CheckResult:
    if not ctx.pairs:
        return CheckResult(
            code="market.pair_tradable", title="Pairs tradable", status=SKIPPED,
            severity=INFO, message="No pairs were supplied to check.",
        )

    try:
        markets = ctx.provider.fetch_markets()
    except ProviderError as exc:
        return CheckResult(
            code="market.pair_tradable", title="Pairs tradable", status=SKIPPED,
            severity=WARN_SEV, message=f"Could not load markets: {exc}",
        )

    missing = [p for p in ctx.pairs if p not in markets]
    inactive = [p for p in ctx.pairs if p in markets and not markets[p].active]

    if missing or inactive:
        return CheckResult(
            code="market.pair_tradable", title="Pairs tradable", status=FAILED,
            severity=CRITICAL,
            message=(
                f"{len(missing)} pair(s) do not exist on this venue"
                f" and {len(inactive)} are delisted or halted."
            ),
            expected={"pairs": ctx.pairs},
            actual={"missing": missing, "inactive": inactive},
            remediation=(
                "Remove these from the whitelist. Pair naming differs between venues "
                "(BTC/USDT vs BTC-USDT vs XBT/USDT), so check the venue's own symbol first."
            ),
        )

    return CheckResult(
        code="market.pair_tradable", title="Pairs tradable", status=PASSED,
        severity=INFO, message=f"All {len(ctx.pairs)} pair(s) exist and are active.",
        actual={"pairs": ctx.pairs},
    )


@timed
def check_min_notional(ctx: CheckContext) -> CheckResult:
    """Would an order of the configured size actually be accepted?

    This is the check that catches the most common silent failure: a stake below
    the venue's minimum, where the bot places orders that are rejected one by one
    and the only symptom is that nothing ever fills.
    """
    if not ctx.pairs:
        return CheckResult(
            code="market.min_notional", title="Stake clears venue minimums",
            status=SKIPPED, severity=INFO, message="No pairs were supplied to check.",
        )

    try:
        markets = ctx.provider.fetch_markets()
    except ProviderError as exc:
        return CheckResult(
            code="market.min_notional", title="Stake clears venue minimums",
            status=SKIPPED, severity=WARN_SEV, message=f"Could not load markets: {exc}",
        )

    offenders: list[dict[str, Any]] = []
    for pair in ctx.pairs:
        market = markets.get(pair)
        if market is None or market.min_cost is None:
            continue
        if ctx.stake_amount < market.min_cost:
            offenders.append(
                {"pair": pair, "min_cost": market.min_cost, "stake": ctx.stake_amount}
            )

    if offenders:
        worst = max(o["min_cost"] for o in offenders)
        return CheckResult(
            code="market.min_notional", title="Stake clears venue minimums",
            status=FAILED, severity=CRITICAL,
            message=(
                f"A stake of {ctx.stake_amount} {ctx.stake_currency} is below the minimum "
                f"order value on {len(offenders)} pair(s)."
            ),
            expected={"min_stake_to_cover_all": worst},
            actual={"stake_amount": ctx.stake_amount, "offenders": offenders},
            remediation=(
                f"Raise stake_amount to at least {worst} {ctx.stake_currency}, or drop these "
                "pairs. Orders below the minimum are rejected by the venue, so the bot "
                "looks like it is running while never actually filling."
            ),
        )

    return CheckResult(
        code="market.min_notional", title="Stake clears venue minimums",
        status=PASSED, severity=INFO,
        message=f"Stake of {ctx.stake_amount} {ctx.stake_currency} clears every pair's minimum.",
    )


@timed
def check_balance_sufficient(ctx: CheckContext) -> CheckResult:
    try:
        balances = ctx.provider.fetch_balances()
    except ProviderGeoBlockError:
        return CheckResult(
            code="balance.sufficient", title="Balance covers open trades",
            status=SKIPPED, severity=WARN_SEV, message="Blocked before balances could be read.",
        )
    except ProviderError as exc:
        return CheckResult(
            code="balance.sufficient", title="Balance covers open trades",
            status=SKIPPED, severity=WARN_SEV, message=f"Could not read balances: {exc}",
        )

    free = next(
        (b.free for b in balances if b.currency.upper() == ctx.stake_currency.upper()), 0.0
    )
    required = ctx.stake_amount * ctx.max_open_trades

    if free < ctx.stake_amount:
        return CheckResult(
            code="balance.sufficient", title="Balance covers open trades",
            status=FAILED, severity=CRITICAL,
            message=(
                f"{free:.4f} {ctx.stake_currency} free, which is less than a single "
                f"{ctx.stake_amount} stake."
            ),
            expected={"minimum": ctx.stake_amount, "comfortable": required},
            actual={"free": free, "currency": ctx.stake_currency},
            remediation=f"Deposit {ctx.stake_currency}, or lower stake_amount.",
        )

    if free < required:
        possible = int(free // ctx.stake_amount) if ctx.stake_amount else 0
        return CheckResult(
            code="balance.sufficient", title="Balance covers open trades",
            status=WARNING, severity=WARN_SEV,
            message=(
                f"{free:.4f} {ctx.stake_currency} free funds only {possible} of "
                f"{ctx.max_open_trades} configured slots."
            ),
            expected={"for_all_slots": required},
            actual={"free": free, "slots_fundable": possible},
            remediation=(
                "Not an error -- the bot will simply hold fewer positions than configured. "
                f"Deposit up to {required:.2f} {ctx.stake_currency} to use every slot."
            ),
        )

    return CheckResult(
        code="balance.sufficient", title="Balance covers open trades",
        status=PASSED, severity=INFO,
        message=f"{free:.4f} {ctx.stake_currency} free covers all {ctx.max_open_trades} slots.",
        actual={"free": free, "required": required},
    )


# ---------------------------------------------------------------------------
# Suites
# ---------------------------------------------------------------------------

CONNECTIVITY_SUITE: tuple[Callable[[CheckContext], CheckResult], ...] = (
    check_reachable,
    check_egress_region,
    check_clock_skew,
    check_credentials,
    check_permissions,
)

PREFLIGHT_SUITE: tuple[Callable[[CheckContext], CheckResult], ...] = (
    check_reachable,
    check_egress_region,
    check_clock_skew,
    check_credentials,
    check_permissions,
    check_pairs_tradable,
    check_min_notional,
    check_balance_sufficient,
)

BALANCE_SUITE: tuple[Callable[[CheckContext], CheckResult], ...] = (
    check_reachable,
    check_credentials,
    check_balance_sufficient,
)

SUITES = {
    "connectivity": CONNECTIVITY_SUITE,
    "preflight": PREFLIGHT_SUITE,
    "balance": BALANCE_SUITE,
}
