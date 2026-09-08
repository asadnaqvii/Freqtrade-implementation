"""Run a verification suite and record what it found.

The engine's job is to turn a list of checks into one persisted validation_run
with its validation_checks rows, and to decide the run's overall verdict. It
never decides *what* to check -- that is checks.SUITES -- and it never talks to a
venue directly.
"""

from __future__ import annotations

import logging
import socket
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Sequence

from app.providers import registry
from app.providers.base import WalletProvider
from app.validation import checks as C
from app.validation.reconcile import reconcile_orders

log = logging.getLogger(__name__)


@dataclass
class ValidationOutcome:
    kind: str
    status: str
    results: list[C.CheckResult]
    summary: str
    context: dict[str, Any]
    duration_ms: int
    egress_ip: str | None = None
    egress_region: str | None = None
    run_id: str | None = None

    @property
    def counts(self) -> dict[str, int]:
        return {
            "total": len(self.results),
            "passed": sum(1 for r in self.results if r.status == C.PASSED),
            "warning": sum(1 for r in self.results if r.status == C.WARNING),
            "failed": sum(1 for r in self.results if r.status in (C.FAILED, C.ERROR)),
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "kind": self.kind,
            "status": self.status,
            "summary": self.summary,
            "counts": self.counts,
            "egress_ip": self.egress_ip,
            "egress_region": self.egress_region,
            "duration_ms": self.duration_ms,
            "checks": [
                {
                    "code": r.code, "title": r.title, "status": r.status,
                    "severity": r.severity, "message": r.message,
                    "expected": r.expected, "actual": r.actual,
                    "remediation": r.remediation, "pair": r.pair,
                    "duration_ms": r.duration_ms,
                }
                for r in self.results
            ],
        }


def _verdict(results: Sequence[C.CheckResult]) -> tuple[str, str]:
    """Overall status plus a one-line summary.

    A run is only 'passed' when nothing failed and nothing warned. Warnings do
    not fail a run, but they must not be invisible either -- a "passed" that
    quietly contained three warnings teaches people to ignore the result.
    """
    failed = [r for r in results if r.status in (C.FAILED, C.ERROR)]
    warned = [r for r in results if r.status == C.WARNING]
    skipped = [r for r in results if r.status == C.SKIPPED]

    if failed:
        critical = [r for r in failed if r.severity == C.CRITICAL]
        lead = (critical or failed)[0]
        return "failed", f"{len(failed)} check(s) failed. First: {lead.title} -- {lead.message}"

    if warned:
        return "warning", f"{len(warned)} warning(s). First: {warned[0].title} -- {warned[0].message}"

    if skipped and len(skipped) == len(results):
        return "skipped", "Nothing could be checked; see the individual checks."

    suffix = f" ({len(skipped)} skipped)" if skipped else ""
    return "passed", f"All {len(results) - len(skipped)} check(s) passed{suffix}."


def run_suite(
    kind: str,
    provider: WalletProvider,
    *,
    pairs: Sequence[str] | None = None,
    stake_currency: str = "USDT",
    stake_amount: float = 10.0,
    max_open_trades: int = 1,
    credentials_held_by: str | None = None,
) -> ValidationOutcome:
    """Execute one named suite against a provider."""
    suite = C.SUITES.get(kind)
    if suite is None:
        raise ValueError(
            f"unknown validation kind {kind!r}; available: {', '.join(sorted(C.SUITES))}"
        )
    return run_suite_with(
        suite, kind=kind, provider=provider, pairs=pairs,
        stake_currency=stake_currency, stake_amount=stake_amount,
        max_open_trades=max_open_trades, credentials_held_by=credentials_held_by,
    )


def run_suite_with(
    suite: Sequence[Any],
    *,
    kind: str,
    provider: WalletProvider,
    pairs: Sequence[str] | None = None,
    stake_currency: str = "USDT",
    stake_amount: float = 10.0,
    max_open_trades: int = 1,
    credentials_held_by: str | None = None,
) -> ValidationOutcome:
    """Execute an explicit list of checks.

    Separate from run_suite because the bot's self-check is not one of the named
    suites: it runs only what needs a key plus the context to interpret it, and
    naming it in SUITES would offer it to callers that cannot run it.
    """
    ctx = C.CheckContext(
        provider=provider,
        pairs=list(pairs or []),
        stake_currency=stake_currency,
        stake_amount=stake_amount,
        max_open_trades=max_open_trades,
        credentials_held_by=credentials_held_by,
    )

    started = time.perf_counter()
    results = [check(ctx) for check in suite]
    duration_ms = int((time.perf_counter() - started) * 1000)

    status, summary = _verdict(results)

    if not provider.is_live:
        # A simulated pass must never read as evidence that real keys work.
        summary = f"{summary} (simulated provider -- no venue was contacted)"

    report = ctx.connectivity
    return ValidationOutcome(
        kind=kind,
        status=status,
        results=results,
        summary=summary,
        duration_ms=duration_ms,
        egress_ip=report.egress_ip if report else None,
        egress_region=report.egress_country if report else None,
        context={
            "provider": provider.describe(),
            "pairs": ctx.pairs,
            "stake_currency": stake_currency,
            "stake_amount": stake_amount,
            "max_open_trades": max_open_trades,
        },
    )


def run_reconciliation(
    provider: WalletProvider,
    bot_orders: Sequence[dict[str, Any]],
    *,
    lookback_days: int = 30,
) -> tuple[ValidationOutcome, list]:
    """Reconcile the bot's orders against the venue and shape it like a suite."""
    started = time.perf_counter()
    findings = reconcile_orders(provider, bot_orders, lookback_days=lookback_days)
    duration_ms = int((time.perf_counter() - started) * 1000)

    matched = [f for f in findings if f.kind == "matched"]
    mismatched = [f for f in findings if f.kind not in ("matched", "unavailable")]
    unavailable = [f for f in findings if f.kind == "unavailable"]

    results: list[C.CheckResult] = [
        C.CheckResult(
            code="reconciliation.coverage",
            title="Orders compared",
            status=C.PASSED if findings else C.SKIPPED,
            severity=C.INFO,
            message=(
                f"{len(matched)} order(s) agree, {len(mismatched)} disagree, "
                f"{len(unavailable)} pair(s) could not be read."
            ),
            actual={"matched": len(matched), "mismatched": len(mismatched)},
        )
    ]

    by_kind: dict[str, list] = {}
    for finding in mismatched:
        by_kind.setdefault(finding.kind, []).append(finding)

    remedies = {
        "missing_in_bot": (
            "Orders exist on the exchange that this bot never placed. Confirm no other "
            "bot or person is trading this account, then rotate the API key if not."
        ),
        "missing_on_exchange": (
            "The bot recorded orders the exchange does not report. Usually a crash "
            "between placing and confirming; check the bot log around these timestamps."
        ),
        "amount": "Partial fills the bot treated as complete. Reported P&L is overstated.",
        "price": "Execution prices differ beyond tolerance, so reported P&L is drifting from real P&L.",
        "status": "Order lifecycle states disagree; the bot may be holding a position it thinks is closed.",
    }

    for kind, group in by_kind.items():
        results.append(
            C.CheckResult(
                code=f"reconciliation.{kind}",
                title=f"Order mismatch: {kind.replace('_', ' ')}",
                status=C.FAILED,
                severity=C.CRITICAL if kind == "missing_in_bot" else C.WARN_SEV,
                message=f"{len(group)} order(s) affected. {group[0].detail}",
                actual={"count": len(group), "pairs": sorted({f.pair for f in group})},
                remediation=remedies.get(kind),
            )
        )

    for finding in unavailable:
        results.append(
            C.CheckResult(
                code="reconciliation.unavailable",
                title="Pair could not be read",
                status=C.SKIPPED, severity=C.WARN_SEV,
                pair=finding.pair, message=finding.detail,
            )
        )

    status, summary = _verdict(results)
    return (
        ValidationOutcome(
            kind="reconciliation", status=status, results=results, summary=summary,
            duration_ms=duration_ms,
            context={"lookback_days": lookback_days, "orders_examined": len(bot_orders)},
        ),
        findings,
    )


def persist(
    client,
    outcome: ValidationOutcome,
    *,
    owner_id: str | None,
    account_id: str | None = None,
    bot_instance_id: str | None = None,
    provider_name: str | None = None,
    reconciliation: Sequence | None = None,
) -> str:
    """Write a run and its checks, and return the run id."""
    counts = outcome.counts
    run = client.insert(
        "validation_runs",
        {
            "owner_id": owner_id,
            "account_id": account_id,
            "bot_instance_id": bot_instance_id,
            "kind": outcome.kind,
            "status": outcome.status,
            "host": socket.gethostname(),
            "egress_ip": outcome.egress_ip,
            "egress_region": outcome.egress_region,
            "provider": provider_name,
            "checks_total": counts["total"],
            "checks_passed": counts["passed"],
            "checks_warning": counts["warning"],
            "checks_failed": counts["failed"],
            "summary": outcome.summary,
            "context": outcome.context,
            "finished_at": "now()",
            "duration_ms": outcome.duration_ms,
        },
    )
    run_id = run[0]["id"]
    outcome.run_id = run_id

    if outcome.results:
        client.insert(
            "validation_checks",
            [r.as_row(run_id) for r in outcome.results],
            returning=False,
        )

    if reconciliation:
        client.insert_chunked(
            "order_reconciliations",
            (f.as_row(run_id, bot_instance_id, account_id) for f in reconciliation),
        )

    # Stamp the account so the UI can show "verified 3 minutes ago" without a join.
    if account_id:
        client.update(
            "exchange_accounts",
            {
                "last_verified_at": "now()",
                "last_verification": outcome.status,
                "verification_notes": outcome.summary,
            },
            filters={"id": f"eq.{account_id}"},
        )

    return run_id


def bot_for_account(client, account: dict[str, Any]) -> dict[str, Any] | None:
    """The bot that holds this account's keys, if there is one.

    Used so a service with no credentials of its own can still verify an
    account: the checks that need a key get asked of the bot instead.

    The lookup runs on the caller's own client, so RLS decides which bots are
    visible and one user's account can never be answered by another user's bot.
    Beyond that, `account_id` is the real link; matching on the exchange is a
    fallback for a bot registered before that column was populated, and it is
    only trusted when it is unambiguous.
    """
    if client is None or not account.get("id"):
        return None

    try:
        linked = client.select(
            "bot_instances",
            columns="id,name,exchange,api_base_url,trading_mode,status,account_id",
            filters={"account_id": f"eq.{account['id']}"},
            limit=2,
        )
        if len(linked) == 1:
            return linked[0]
        if len(linked) > 1:
            log.info("account %s has %d bots linked; not delegating",
                     account.get("label"), len(linked))
            return None

        provider = (account.get("provider") or "").lower()
        if not provider:
            return None
        candidates = [
            bot for bot in client.select(
                "bot_instances",
                columns="id,name,exchange,api_base_url,trading_mode,status,account_id",
                filters={"exchange": f"eq.{provider}"},
                limit=5,
            )
            if bot.get("api_base_url") and not bot.get("account_id")
        ]
        if len(candidates) == 1:
            log.info("delegating %s to unlinked bot %s matched on exchange",
                     account.get("label"), candidates[0].get("name"))
            return candidates[0]
    except Exception as exc:  # noqa: BLE001 - a failed lookup must not fail the run
        log.info("could not resolve a bot for account %s: %s", account.get("label"), exc)
    return None


#: Checks that cannot run without a key, and so are answered by the bot.
DELEGATED_CODES = ("provider.credentials", "provider.permissions", "balance.sufficient")

#: How old a bot-measured result may be before it is shown as stale rather than
#: current. The bot re-checks every few minutes, so anything past this means it
#: stopped checking, and a stale pass is exactly the kind of reassurance that
#: should not be given silently.
BOT_FINDING_MAX_AGE_SECONDS = 30 * 60


def _age_seconds(timestamp: str | None) -> float | None:
    if not timestamp:
        return None
    try:
        when = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - when).total_seconds()


def _describe_age(seconds: float | None) -> str:
    if seconds is None:
        return "at an unknown time"
    if seconds < 90:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)} min ago"
    return f"{int(seconds // 3600)}h ago"


def merge_bot_findings(client, outcome: ValidationOutcome, *, account, bot) -> None:
    """Replace the locally-unanswerable checks with what the bot measured.

    Every merged result says which bot measured it and how long ago, because a
    credential check that silently reports a half-hour-old observation as
    current is worse than one that admits it could not look.
    """
    findings = latest_bot_findings(client, account_id=account.get("id"), bot_id=bot.get("id"))
    if not findings:
        return

    age = _age_seconds(findings.get("created_at"))
    stale = age is not None and age > BOT_FINDING_MAX_AGE_SECONDS
    when = _describe_age(age)
    by_code = {row.get("code"): row for row in findings.get("checks") or []}

    for index, result in enumerate(outcome.results):
        row = by_code.get(result.code)
        if result.code not in DELEGATED_CODES or not row:
            continue

        status = row.get("status") or C.SKIPPED
        message = (row.get("message") or "").rstrip()
        if stale:
            # Do not carry a pass forward past its shelf life.
            status = C.SKIPPED if status == C.PASSED else status
            suffix = f"Measured by {bot.get('name')} {when} -- too old to rely on."
        else:
            suffix = f"Measured by {bot.get('name')} {when}."

        outcome.results[index] = C.CheckResult(
            code=result.code,
            title=result.title,
            status=status,
            severity=row.get("severity") or result.severity,
            message=f"{message} {suffix}".strip(),
            expected=row.get("expected"),
            actual=row.get("actual"),
            remediation=row.get("remediation"),
            pair=row.get("pair"),
            duration_ms=row.get("duration_ms"),
        )

    outcome.status, outcome.summary = _verdict(outcome.results)
    outcome.context["credentials_measured_by"] = {
        "bot": bot.get("name"),
        "run_id": findings.get("run_id"),
        "age_seconds": int(age) if age is not None else None,
        "stale": stale,
    }


def latest_bot_findings(client, *, account_id, bot_id) -> dict[str, Any] | None:
    """The most recent verification this bot ran on its own credentials."""
    if client is None or not account_id:
        return None
    try:
        runs = client.select(
            "validation_runs",
            columns="id,created_at,status",
            filters={
                "account_id": f"eq.{account_id}",
                "bot_instance_id": f"eq.{bot_id}" if bot_id else "not.is.null",
            },
            order="created_at.desc",
            limit=1,
        )
        if not runs:
            return None
        checks = client.select(
            "validation_checks",
            columns="code,status,severity,message,expected,actual,remediation,pair,duration_ms",
            filters={"run_id": f"eq.{runs[0]['id']}"},
        )
        return {"run_id": runs[0]["id"], "created_at": runs[0].get("created_at"),
                "checks": checks}
    except Exception as exc:  # noqa: BLE001 - a missing overlay must not fail the run
        log.info("could not read bot findings for account %s: %s", account_id, exc)
    return None


def verify_account(
    client,
    account: dict[str, Any],
    *,
    kind: str = "preflight",
    pairs: Sequence[str] | None = None,
    stake_currency: str = "USDT",
    stake_amount: float = 10.0,
    max_open_trades: int = 1,
    persist_result: bool = True,
) -> ValidationOutcome:
    """End-to-end: build the provider, run the suite, record the outcome.

    On the public service the credential-dependent checks cannot run locally --
    there is no key here, by design. They are answered by the bot that holds the
    key, which verifies itself and writes the result to the database; this merges
    that result in rather than reporting a missing key as a bad one.
    """
    provider = registry.build(account)
    bot = None
    if not provider.credentials.present:
        bot = bot_for_account(client, account)

    try:
        outcome = run_suite(
            kind, provider,
            pairs=pairs, stake_currency=stake_currency,
            stake_amount=stake_amount, max_open_trades=max_open_trades,
            credentials_held_by=(bot or {}).get("name"),
        )
    finally:
        provider.close()

    if bot:
        merge_bot_findings(client, outcome, account=account, bot=bot)

    if persist_result and client is not None:
        persist(
            client, outcome,
            owner_id=account.get("owner_id"),
            account_id=account.get("id"),
            provider_name=account.get("provider"),
        )
    return outcome
