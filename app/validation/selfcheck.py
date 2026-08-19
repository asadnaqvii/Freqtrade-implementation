"""The bot verifies its own credentials, so the app never needs them.

The public service holds no exchange keys on purpose. That leaves it unable to
answer three questions -- are the keys valid, what may they do, is there enough
balance -- and the obvious fixes are both bad: copying the keys to the public
service defeats the split, and giving it freqtrade's API login would let a
compromise of the public surface place orders.

So the bot answers them where the keys already are, and writes the answer to the
database the app already reads. Nothing new is exposed and no secret moves.

Two things this deliberately does not do:

  * It does not use freqtrade's dry-run state. A dry-run bot still has real
    keys, so the check talks to the venue directly through ccxt and reports what
    the venue actually says. Verification you can only run in production is
    verification you run for the first time when it matters.

  * It does not report a stale result as current. Each run is timestamped and
    the reader decides; see engine.merge_bot_findings.
"""

from __future__ import annotations

import logging
from typing import Any

from app.providers import credentials as creds
from app.providers import registry
from app.validation import checks as C
from app.validation import engine

log = logging.getLogger(__name__)

#: What the bot can answer that the app cannot. Deliberately the credential
#: checks only -- the app measures its own connectivity, and reporting the bot's
#: egress as the app's would be a wrong answer dressed as a helpful one.
SELFCHECK_SUITE = (
    C.check_reachable,
    C.check_egress_region,
    C.check_clock_skew,
    C.check_credentials,
    C.check_permissions,
    C.check_balance_sufficient,
)


def run(
    client,
    *,
    account: dict[str, Any],
    bot_instance_id: str | None,
    owner_id: str | None,
    stake_currency: str = "USDT",
    stake_amount: float = 10.0,
    max_open_trades: int = 1,
) -> engine.ValidationOutcome | None:
    """Verify this deployment's own credentials and record the result.

    Returns None when there is nothing to check, which is the honest outcome for
    a bot with no keys -- not a failure.
    """
    resolved = creds.resolve(account)
    if not resolved.present:
        log.info("self-check skipped: this bot has no exchange credentials")
        return None

    provider = registry.build(account, credentials=resolved)
    try:
        outcome = engine.run_suite_with(
            SELFCHECK_SUITE,
            kind="connectivity",
            provider=provider,
            stake_currency=stake_currency,
            stake_amount=stake_amount,
            max_open_trades=max_open_trades,
        )
    finally:
        provider.close()

    try:
        engine.persist(
            client, outcome,
            owner_id=owner_id,
            account_id=account.get("id"),
            bot_instance_id=bot_instance_id,
            provider_name=account.get("provider"),
        )
        _stamp_account(client, account, outcome)
    except Exception as exc:  # noqa: BLE001 - the bot must trade regardless
        log.warning("could not record the self-check: %s", exc)

    return outcome


def _stamp_account(client, account: dict[str, Any], outcome) -> None:
    """Put the headline on the account row, so the wallet list shows it."""
    if not account.get("id"):
        return

    permissions: list[str] = []
    for result in outcome.results:
        if result.code == "provider.permissions" and isinstance(result.actual, dict):
            permissions = list(result.actual.get("permissions") or [])

    values: dict[str, Any] = {
        "last_verified_at": "now()",
        "last_verification": outcome.status,
        "verification_notes": outcome.summary,
    }
    if permissions:
        values["permissions"] = permissions

    client.update("exchange_accounts", values, filters={"id": f"eq.{account['id']}"})
