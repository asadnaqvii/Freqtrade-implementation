"""Tests for verifying a wallet from a service that holds no keys.

The public app deliberately has no exchange credentials, so it cannot answer
"do these keys work". The bot that holds them answers instead and writes the
result to the database. Three properties are load-bearing:

  * a missing key *here* must not be reported as a bad key. Those are different
    problems with different fixes, and conflating them sends someone off
    rotating credentials that were never wrong.
  * a bot-measured pass must expire. Reporting a half-hour-old observation as
    current is the failure that would let a broken key look healthy.
  * the merge must not invent anything the bot did not measure.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.providers.base import (
    ConnectivityReport,
    Credentials,
    MarketInfo,
    WalletProvider,
)
from app.validation import checks as C
from app.validation import engine


class KeylessProvider(WalletProvider):
    """What the public app builds: a real venue driver with no credentials."""

    name = "kucoin"
    is_live = True

    def check_connectivity(self):
        return ConnectivityReport(
            reachable=True, geo_blocked=False, latency_ms=11.0,
            server_time_skew_seconds=0.2, egress_ip="74.220.52.215", egress_country="SG",
        )

    def verify_credentials(self):
        raise AssertionError("must not be called without a key")

    def permissions(self):
        raise AssertionError("must not be called without a key")

    def fetch_balances(self):
        raise AssertionError("must not be called without a key")

    def fetch_markets(self):
        return {
            "BTC/USDT": MarketInfo(
                symbol="BTC/USDT", base="BTC", quote="USDT", spot=True,
                active=True, min_cost=1.0,
            )
        }

    def fetch_orders(self, symbol, *, since=None, limit=100):
        raise AssertionError("must not be called without a key")


def ctx(**kwargs):
    return C.CheckContext(provider=KeylessProvider(), **kwargs)


# ---------------------------------------------------------------------------
# Missing here is not the same as broken
# ---------------------------------------------------------------------------

def test_no_key_anywhere_still_fails():
    result = C.check_credentials(ctx())
    assert result.status == C.FAILED
    assert result.severity == C.CRITICAL


def test_a_key_held_by_the_bot_is_not_a_failure():
    result = C.check_credentials(ctx(credentials_held_by="freqtrade-bot"))
    assert result.status == C.SKIPPED
    assert "freqtrade-bot" in result.message


def test_permissions_defer_to_the_bot_too():
    result = C.check_permissions(ctx(credentials_held_by="freqtrade-bot"))
    assert result.status == C.SKIPPED
    assert "freqtrade-bot" in result.message


# ---------------------------------------------------------------------------
# Merging what the bot measured
# ---------------------------------------------------------------------------

def outcome_with(*results):
    status, summary = engine._verdict(results)
    return engine.ValidationOutcome(
        kind="preflight", status=status, results=list(results),
        summary=summary, context={}, duration_ms=1,
    )


class FakeDB:
    def __init__(self, *, created_at, checks):
        self._created_at = created_at
        self._checks = checks

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        if table == "validation_runs":
            return [{"id": "run-1", "created_at": self._created_at, "status": "passed"}]
        return self._checks


PASSED_CREDENTIALS = [{
    "code": "provider.credentials", "status": "passed", "severity": "info",
    "message": "The venue accepted an authenticated request.",
    "expected": None, "actual": {"currencies": 4}, "remediation": None,
    "pair": None, "duration_ms": 120,
}]


def fresh(seconds_ago):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)).isoformat()


def test_a_recent_bot_pass_is_adopted():
    outcome = outcome_with(C.check_credentials(ctx(credentials_held_by="freqtrade-bot")))
    db = FakeDB(created_at=fresh(120), checks=PASSED_CREDENTIALS)

    engine.merge_bot_findings(
        db, outcome, account={"id": "acct-1"}, bot={"id": "bot-1", "name": "freqtrade-bot"}
    )

    merged = outcome.results[0]
    assert merged.status == C.PASSED
    assert "freqtrade-bot" in merged.message
    assert "2 min ago" in merged.message
    assert outcome.status == "passed"
    assert outcome.context["credentials_measured_by"]["stale"] is False


def test_a_stale_bot_pass_is_not_adopted():
    outcome = outcome_with(C.check_credentials(ctx(credentials_held_by="freqtrade-bot")))
    db = FakeDB(created_at=fresh(4 * 3600), checks=PASSED_CREDENTIALS)

    engine.merge_bot_findings(
        db, outcome, account={"id": "acct-1"}, bot={"id": "bot-1", "name": "freqtrade-bot"}
    )

    merged = outcome.results[0]
    assert merged.status == C.SKIPPED, "an old pass must not read as a current one"
    assert "too old" in merged.message
    assert outcome.context["credentials_measured_by"]["stale"] is True


def test_a_stale_failure_is_still_reported():
    # Expiry protects against false reassurance, not against bad news.
    failing = [dict(PASSED_CREDENTIALS[0], status="failed", severity="critical",
                    message="Invalid API key.")]
    outcome = outcome_with(C.check_credentials(ctx(credentials_held_by="freqtrade-bot")))
    db = FakeDB(created_at=fresh(4 * 3600), checks=failing)

    engine.merge_bot_findings(
        db, outcome, account={"id": "acct-1"}, bot={"id": "bot-1", "name": "freqtrade-bot"}
    )
    assert outcome.results[0].status == C.FAILED


def test_checks_the_bot_did_not_measure_are_left_alone():
    local = C.check_reachable(ctx(credentials_held_by="freqtrade-bot"))
    outcome = outcome_with(local)
    db = FakeDB(created_at=fresh(60), checks=PASSED_CREDENTIALS)

    engine.merge_bot_findings(
        db, outcome, account={"id": "acct-1"}, bot={"id": "bot-1", "name": "freqtrade-bot"}
    )
    assert outcome.results[0].message == local.message


def test_no_bot_result_leaves_the_skip_in_place():
    class Empty:
        def select(self, *a, **k):
            return []

    outcome = outcome_with(C.check_credentials(ctx(credentials_held_by="freqtrade-bot")))
    engine.merge_bot_findings(
        Empty(), outcome, account={"id": "acct-1"},
        bot={"id": "bot-1", "name": "freqtrade-bot"},
    )
    assert outcome.results[0].status == C.SKIPPED


def test_a_broken_lookup_does_not_fail_the_run():
    class Exploding:
        def select(self, *a, **k):
            raise RuntimeError("postgrest is down")

    outcome = outcome_with(C.check_credentials(ctx(credentials_held_by="freqtrade-bot")))
    engine.merge_bot_findings(
        Exploding(), outcome, account={"id": "acct-1"},
        bot={"id": "bot-1", "name": "freqtrade-bot"},
    )
    assert outcome.results[0].status == C.SKIPPED


# ---------------------------------------------------------------------------
# The bot's own run
# ---------------------------------------------------------------------------

def test_selfcheck_declines_when_the_bot_has_no_keys(monkeypatch):
    from app.validation import selfcheck

    monkeypatch.delenv("FREQTRADE__EXCHANGE__KEY", raising=False)
    monkeypatch.delenv("FREQTRADE__EXCHANGE__SECRET", raising=False)
    assert selfcheck.run(
        None, account={"provider": "kucoin"}, bot_instance_id=None, owner_id=None
    ) is None


def test_selfcheck_covers_exactly_the_checks_the_app_cannot_run():
    from app.validation import selfcheck

    assert C.check_credentials in selfcheck.SELFCHECK_SUITE
    assert C.check_permissions in selfcheck.SELFCHECK_SUITE
    assert C.check_balance_sufficient in selfcheck.SELFCHECK_SUITE
    # and not the market checks, which need no key and belong to the caller
    assert C.check_pairs_tradable not in selfcheck.SELFCHECK_SUITE


# ---------------------------------------------------------------------------
# Choosing which bot answers -- the multi-user seam
# ---------------------------------------------------------------------------

class LookupDB:
    def __init__(self, by_filter):
        self._by_filter = by_filter

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        for key, rows in self._by_filter:
            if key in (filters or {}):
                return rows
        return []


def test_the_linked_bot_is_preferred():
    db = LookupDB([("account_id", [{"id": "bot-1", "name": "a", "api_base_url": "x"}])])
    assert engine.bot_for_account(db, {"id": "acct-1", "provider": "kucoin"})["id"] == "bot-1"


def test_an_ambiguous_exchange_match_is_refused():
    db = LookupDB([
        ("account_id", []),
        ("exchange", [
            {"id": "bot-1", "api_base_url": "x", "account_id": None},
            {"id": "bot-2", "api_base_url": "y", "account_id": None},
        ]),
    ])
    assert engine.bot_for_account(db, {"id": "a", "provider": "kucoin"}) is None


def test_a_bot_belonging_to_another_account_is_not_borrowed():
    db = LookupDB([
        ("account_id", []),
        ("exchange", [{"id": "bot-1", "api_base_url": "x", "account_id": "other"}]),
    ])
    assert engine.bot_for_account(db, {"id": "a", "provider": "kucoin"}) is None


# ---------------------------------------------------------------------------
# Reconciliation: past trades against the venue's record
# ---------------------------------------------------------------------------

def test_reconciliation_declines_without_keys(monkeypatch):
    from app.validation import selfcheck

    monkeypatch.delenv("FREQTRADE__EXCHANGE__KEY", raising=False)
    monkeypatch.delenv("FREQTRADE__EXCHANGE__SECRET", raising=False)
    assert selfcheck.reconcile(
        None, account={"provider": "kucoin"}, bot_instance_id=None, owner_id=None
    ) is None


def test_reconciliation_does_nothing_before_there_are_orders(monkeypatch):
    from app.validation import selfcheck

    monkeypatch.setenv("FREQTRADE__EXCHANGE__KEY", "k")
    monkeypatch.setenv("FREQTRADE__EXCHANGE__SECRET", "s")

    class Empty:
        def select(self, *a, **k):
            return []

    assert selfcheck.reconcile(
        Empty(), account={"provider": "kucoin"}, bot_instance_id=None, owner_id=None
    ) is None


def test_a_missing_order_view_is_not_a_failure(monkeypatch):
    # v_live_orders only exists once freqtrade has created its tables.
    from app.validation import selfcheck

    monkeypatch.setenv("FREQTRADE__EXCHANGE__KEY", "k")
    monkeypatch.setenv("FREQTRADE__EXCHANGE__SECRET", "s")

    class NoView:
        def select(self, *a, **k):
            raise RuntimeError('relation "v_live_orders" does not exist')

    assert selfcheck.reconcile(
        NoView(), account={"provider": "kucoin"}, bot_instance_id=None, owner_id=None
    ) is None


def test_an_order_the_bot_never_placed_is_the_serious_finding():
    """Someone or something else trading the account is the case that matters.

    A partial fill is an accounting error; an order the bot did not place means
    the key is in use somewhere it should not be.
    """
    from app.validation.reconcile import Discrepancy

    finding = Discrepancy(pair="BTC/USDT", kind="missing_in_bot",
                          ft_order_id=None, exchange_order_id="abc",
                          detail="the venue has an order this bot never recorded")
    row = finding.as_row("run-1", "bot-1", "acct-1")
    assert row["matched"] is False
    assert row["discrepancy_kind"] == "missing_in_bot"
    assert row["exchange_order_id"] == "abc"
    assert row["notes"]


def test_a_matching_order_stores_no_discrepancy_kind():
    from app.validation.reconcile import Discrepancy

    row = Discrepancy(pair="BTC/USDT", kind="matched", ft_order_id=1,
                      exchange_order_id="abc", detail="agrees").as_row("r", "b", "a")
    assert row["matched"] is True
    assert row["discrepancy_kind"] is None
