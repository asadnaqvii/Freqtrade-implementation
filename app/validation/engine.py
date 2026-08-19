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
) -> ValidationOutcome:
    """Execute one named suite against a provider."""
    suite = C.SUITES.get(kind)
    if suite is None:
        raise ValueError(
            f"unknown validation kind {kind!r}; available: {', '.join(sorted(C.SUITES))}"
        )

    ctx = C.CheckContext(
        provider=provider,
        pairs=list(pairs or []),
        stake_currency=stake_currency,
        stake_amount=stake_amount,
        max_open_trades=max_open_trades,
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
    """End-to-end: build the provider, run the suite, record the outcome."""
    provider = registry.build(account)
    try:
        outcome = run_suite(
            kind, provider,
            pairs=pairs, stake_currency=stake_currency,
            stake_amount=stake_amount, max_open_trades=max_open_trades,
        )
    finally:
        provider.close()

    if persist_result and client is not None:
        persist(
            client, outcome,
            owner_id=account.get("owner_id"),
            account_id=account.get("id"),
            provider_name=account.get("provider"),
        )
    return outcome
