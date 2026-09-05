"""Tests for wallet providers and the verification engine.

The geo-block tests matter most: distinguishing "the venue refused where you are"
from "the venue is down" is the difference between a fix that takes five minutes
and an afternoon of rotating keys that were never the problem.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.providers import registry
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
from app.providers.ccxt_provider import CcxtProvider, _is_geo_block
from app.providers.kucoin import KuCoinProvider
from app.validation import checks as C
from app.validation import engine
from app.validation.reconcile import reconcile_orders


# ---------------------------------------------------------------------------
# Geo-block detection
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "message",
    [
        "kucoin {\"code\":\"403\",\"msg\":\"Access denied\"}",
        "Service unavailable from a restricted location",
        "This service is not available in your region",
        "Your country is not supported",
        "403 Forbidden",
        "ip is not allowed",
    ],
)
def test_geo_block_messages_are_recognised(message):
    assert _is_geo_block(message)


@pytest.mark.parametrize(
    "message",
    [
        "Invalid API key",
        "Connection timed out",
        "Insufficient balance",
        "Order size too small",
    ],
)
def test_ordinary_errors_are_not_mistaken_for_geo_blocks(message):
    assert not _is_geo_block(message)


def test_geo_block_translates_to_its_own_error_type():
    import ccxt

    provider = CcxtProvider("kucoin")
    translated = provider._translate(ccxt.ExchangeError("Access denied from a restricted location"))
    assert isinstance(translated, ProviderGeoBlockError)


def test_bad_key_translates_to_auth_error_not_geo_block():
    import ccxt

    provider = CcxtProvider("kucoin")
    translated = provider._translate(ccxt.AuthenticationError("Invalid API key"))
    assert isinstance(translated, ProviderAuthError)
    assert not isinstance(translated, ProviderGeoBlockError)


def test_network_failure_translates_to_unavailable():
    import ccxt

    provider = CcxtProvider("kucoin")
    translated = provider._translate(ccxt.NetworkError("connection reset"))
    assert isinstance(translated, ProviderUnavailableError)


def test_kucoin_geo_block_carries_the_region_remedy():
    provider = KuCoinProvider()
    import ccxt

    translated = provider._translate(ccxt.ExchangeError("restricted location"))
    assert isinstance(translated, ProviderGeoBlockError)
    assert "non-US region" in (translated.detail or "")
    assert "fixed at creation" in (translated.detail or "")


# ---------------------------------------------------------------------------
# Fakes for the engine
# ---------------------------------------------------------------------------

class FakeProvider(WalletProvider):
    name = "fake"

    def __init__(self, *, geo_blocked=False, permissions=None, balance=1000.0,
                 markets=None, skew=0.0, orders=None, credentials=None):
        super().__init__(credentials or Credentials(key="k", secret="s"))
        self._geo = geo_blocked
        self._permissions = permissions if permissions is not None else {"read", "trade"}
        self._balance = balance
        self._markets = markets
        self._skew = skew
        self._orders = orders or []

    def check_connectivity(self):
        return ConnectivityReport(
            reachable=not self._geo, geo_blocked=self._geo, latency_ms=12.0,
            server_time_skew_seconds=None if self._geo else self._skew,
            egress_ip="203.0.113.7", egress_country="US" if self._geo else "SG",
            detail="blocked" if self._geo else None,
        )

    def verify_credentials(self):
        if self._geo:
            raise ProviderGeoBlockError("blocked", venue="fake")
        return {"ok": True}

    def permissions(self):
        if self._geo:
            raise ProviderGeoBlockError("blocked", venue="fake")
        return set(self._permissions)

    def fetch_balances(self):
        return [Balance("USDT", self._balance, 0.0, self._balance)]

    def fetch_markets(self):
        if self._markets is not None:
            return self._markets
        return {
            "BTC/USDT": MarketInfo("BTC/USDT", "BTC", "USDT", True, True, min_cost=1.0),
            "ETH/USDT": MarketInfo("ETH/USDT", "ETH", "USDT", True, True, min_cost=1.0),
        }

    def fetch_orders(self, symbol, *, since=None, limit=100):
        return [o for o in self._orders if o.symbol == symbol]


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

def test_withdrawal_permission_is_a_critical_failure():
    outcome = engine.run_suite(
        "connectivity", FakeProvider(permissions={"read", "trade", "withdraw"})
    )
    perm = next(r for r in outcome.results if r.code == "provider.permissions")
    assert perm.status == C.FAILED
    assert perm.severity == C.CRITICAL
    assert "withdraw" in perm.message.lower()
    assert outcome.status == "failed"


def test_read_and_trade_only_key_passes():
    outcome = engine.run_suite("connectivity", FakeProvider(permissions={"read", "trade"}))
    perm = next(r for r in outcome.results if r.code == "provider.permissions")
    assert perm.status == C.PASSED


def test_read_only_key_warns_rather_than_fails():
    outcome = engine.run_suite("connectivity", FakeProvider(permissions={"read"}))
    perm = next(r for r in outcome.results if r.code == "provider.permissions")
    assert perm.status == C.WARNING


def test_geo_block_fails_the_run_and_names_the_region():
    outcome = engine.run_suite("connectivity", FakeProvider(geo_blocked=True))
    assert outcome.status == "failed"
    geo = next(r for r in outcome.results if r.code == "provider.geo_block")
    assert geo.severity == C.CRITICAL
    assert geo.actual["egress_country"] == "US"
    region = next(r for r in outcome.results if r.code == "provider.egress_region")
    assert region.status == C.FAILED


def test_non_us_egress_passes_the_region_check():
    outcome = engine.run_suite("connectivity", FakeProvider())
    region = next(r for r in outcome.results if r.code == "provider.egress_region")
    assert region.status == C.PASSED
    assert region.actual["egress_country"] == "SG"


def test_clock_skew_beyond_the_limit_fails():
    outcome = engine.run_suite("connectivity", FakeProvider(skew=12.0))
    skew = next(r for r in outcome.results if r.code == "provider.clock_skew")
    assert skew.status == C.FAILED
    assert "NTP" in (skew.remediation or "")


def test_stake_below_venue_minimum_fails_with_the_required_amount():
    markets = {
        "BTC/USDT": MarketInfo("BTC/USDT", "BTC", "USDT", True, True, min_cost=25.0),
    }
    outcome = engine.run_suite(
        "preflight", FakeProvider(markets=markets), pairs=["BTC/USDT"], stake_amount=10.0
    )
    check = next(r for r in outcome.results if r.code == "market.min_notional")
    assert check.status == C.FAILED
    assert check.expected["min_stake_to_cover_all"] == 25.0
    assert "25.0" in (check.remediation or "")


def test_unknown_pair_fails_the_tradability_check():
    outcome = engine.run_suite(
        "preflight", FakeProvider(), pairs=["BTC/USDT", "NOTREAL/USDT"]
    )
    check = next(r for r in outcome.results if r.code == "market.pair_tradable")
    assert check.status == C.FAILED
    assert check.actual["missing"] == ["NOTREAL/USDT"]


def test_balance_below_one_stake_fails():
    outcome = engine.run_suite(
        "preflight", FakeProvider(balance=2.0), pairs=["BTC/USDT"],
        stake_amount=10.0, max_open_trades=3,
    )
    check = next(r for r in outcome.results if r.code == "balance.sufficient")
    assert check.status == C.FAILED


def test_balance_covering_some_slots_warns_rather_than_fails():
    outcome = engine.run_suite(
        "preflight", FakeProvider(balance=25.0), pairs=["BTC/USDT"],
        stake_amount=10.0, max_open_trades=6,
    )
    check = next(r for r in outcome.results if r.code == "balance.sufficient")
    assert check.status == C.WARNING
    assert check.actual["slots_fundable"] == 2


def test_a_check_that_raises_becomes_an_error_not_a_crash():
    class Exploding(FakeProvider):
        def fetch_balances(self):
            raise RuntimeError("boom")

    outcome = engine.run_suite("balance", Exploding())
    assert outcome.status in ("failed", "warning")
    assert len(outcome.results) == 3  # the suite still completed


def test_simulated_provider_is_labelled_in_the_summary():
    outcome = engine.run_suite("preflight", registry.build({"provider": "paper"}),
                               pairs=["BTC/USDT"])
    assert "simulated" in outcome.summary.lower()


def test_unknown_suite_name_is_rejected():
    with pytest.raises(ValueError) as exc:
        engine.run_suite("nonsense", FakeProvider())
    assert "unknown validation kind" in str(exc.value)


# ---------------------------------------------------------------------------
# Reconciliation
# ---------------------------------------------------------------------------

def _ago(**kw):
    return datetime.now(timezone.utc) - timedelta(**kw)


def _order(order_id, **kw):
    defaults = dict(
        symbol="BTC/USDT", side="buy", status="closed", order_type="limit",
        price=100.0, average=100.0, amount=1.0, filled=1.0, remaining=0.0,
        cost=100.0, fee_cost=0.1, fee_currency="USDT",
        timestamp=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return OrderInfo(order_id=order_id, **defaults)


def test_matching_orders_report_agreement():
    provider = FakeProvider(orders=[_order("X1")])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "X1",
            "filled": 1.0, "average": 100.0, "status": "closed"}]
    findings = reconcile_orders(provider, bot)
    assert [f.kind for f in findings] == ["matched"]


def test_partial_fill_recorded_as_complete_is_caught():
    provider = FakeProvider(orders=[_order("X1", filled=0.5)])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "X1",
            "filled": 1.0, "average": 100.0, "status": "closed"}]
    findings = reconcile_orders(provider, bot)
    assert any(f.kind == "amount" for f in findings)


def test_orders_predating_the_bots_history_are_not_rogue_orders():
    """The cutover boundary, which cried wolf every fifteen minutes.

    The venue is queried thirty days back. This bot's database began on 20 Aug,
    so the query reached nine days further back than the bot had existed, and
    every order the retired Railway instance had placed came back as an order
    "the bot has no record of ... a compromised key" -- 35 of them, forever.
    An order placed before the bot kept records is not evidence of anything.
    """
    provider = FakeProvider(orders=[
        _order("RAILWAY_ERA", timestamp=_ago(days=25)),
        _order("OURS", timestamp=_ago(days=3)),
    ])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "OURS",
            "filled": 1.0, "average": 100.0, "status": "closed",
            "order_date": (_ago(days=10)).replace(tzinfo=None).isoformat()}]
    findings = reconcile_orders(provider, bot)
    assert [f.exchange_order_id for f in findings if f.kind == "missing_in_bot"] == []


def test_an_unknown_order_after_the_floor_is_still_flagged():
    """The floor must not become a blanket excuse: inside the bot's own history
    an order it did not place is exactly what this check exists to find."""
    provider = FakeProvider(orders=[
        _order("INTRUDER", timestamp=_ago(days=2)),
        _order("OURS", timestamp=_ago(days=3)),
    ])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "OURS",
            "filled": 1.0, "average": 100.0, "status": "closed",
            "order_date": (_ago(days=10)).replace(tzinfo=None).isoformat()}]
    findings = reconcile_orders(provider, bot)
    assert [f.exchange_order_id for f in findings
            if f.kind == "missing_in_bot"] == ["INTRUDER"]


def test_an_order_placed_seconds_ago_is_a_race_not_an_intruder():
    """Seen on PIEVERSE/USDT at 2026-08-28 01:19:14, nine seconds after the
    order was placed: the venue had accepted it and freqtrade had not yet
    committed its row. Reported once as a possible compromised key, never
    again."""
    provider = FakeProvider(orders=[_order("JUST_PLACED", timestamp=_ago(seconds=9))])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "OTHER",
            "filled": 1.0, "average": 100.0, "status": "closed",
            "order_date": (_ago(days=10)).replace(tzinfo=None).isoformat()}]
    findings = reconcile_orders(provider, bot)
    assert not any(f.kind == "missing_in_bot" for f in findings)


def test_a_naive_order_date_is_read_as_utc():
    """v_live_orders.order_date is `timestamp without time zone` and freqtrade
    stores UTC in it. Reading it as local time would move the floor by hours and
    let real findings through, or hide them."""
    from app.validation.reconcile import _as_utc

    naive = _as_utc("2026-08-20T09:01:36.752")
    aware = _as_utc("2026-08-20T09:01:36.752+00:00")
    assert naive == aware
    assert naive.tzinfo is not None


def test_an_explicit_floor_overrides_the_derived_one():
    provider = FakeProvider(orders=[_order("OLD", timestamp=_ago(days=5))])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "OURS",
            "filled": 1.0, "average": 100.0, "status": "closed",
            "order_date": (_ago(days=30)).replace(tzinfo=None).isoformat()}]
    assert any(f.kind == "missing_in_bot" for f in reconcile_orders(provider, bot))
    assert not any(f.kind == "missing_in_bot" for f in
                   reconcile_orders(provider, bot, history_floor=_ago(days=1)))


def test_order_the_bot_never_placed_is_flagged_as_critical():
    provider = FakeProvider(orders=[_order("GHOST", timestamp=_ago(hours=2))])
    outcome, findings = engine.run_reconciliation(provider, [
        {"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": None,
         "filled": 1.0, "average": 100.0, "status": "closed"}
    ])
    assert any(f.kind == "missing_in_bot" for f in findings)
    check = next(r for r in outcome.results if r.code == "reconciliation.missing_in_bot")
    assert check.severity == C.CRITICAL
    assert "rotate the API key" in (check.remediation or "")


def test_price_within_tolerance_is_not_a_discrepancy():
    provider = FakeProvider(orders=[_order("X1", average=100.05)])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "X1",
            "filled": 1.0, "average": 100.0, "status": "closed"}]
    findings = reconcile_orders(provider, bot)
    assert [f.kind for f in findings] == ["matched"]


def test_price_beyond_tolerance_is_a_discrepancy():
    provider = FakeProvider(orders=[_order("X1", average=110.0)])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "X1",
            "filled": 1.0, "average": 100.0, "status": "closed"}]
    findings = reconcile_orders(provider, bot)
    assert any(f.kind == "price" for f in findings)


def test_closed_and_filled_are_treated_as_the_same_status():
    provider = FakeProvider(orders=[_order("X1", status="filled")])
    bot = [{"ft_order_id": 1, "pair": "BTC/USDT", "exchange_order_id": "X1",
            "filled": 1.0, "average": 100.0, "status": "closed"}]
    findings = reconcile_orders(provider, bot)
    assert [f.kind for f in findings] == ["matched"]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_returns_the_kucoin_subclass_for_kucoin():
    assert isinstance(registry.build({"provider": "kucoin"}), KuCoinProvider)


def test_registry_falls_back_to_generic_ccxt_for_any_other_venue():
    provider = registry.build({"provider": "binance", "ccxt_id": "binance"})
    assert isinstance(provider, CcxtProvider)
    assert provider.ccxt_id == "binance"


def test_registry_exposes_many_venues():
    # The point of the ccxt path is that "any wallet provider" is not aspirational.
    assert len(registry.available()) > 50


def test_credentials_repr_never_leaks_the_key():
    creds = Credentials(key="SUPERSECRETKEY", secret="alsosecret")
    assert "SUPERSECRETKEY" not in repr(creds)
    assert "alsosecret" not in repr(creds)


# ---------------------------------------------------------------------------
# Permission introspection: not knowing must not read as knowing
# ---------------------------------------------------------------------------

class FakeKuCoinExchange:
    """Stands in for ccxt's kucoin, returning whatever the test supplies."""

    def __init__(self, responses):
        self._responses = responses
        self.called = []

    def _answer(self, name):
        self.called.append(name)
        value = self._responses.get(name, KeyError)
        if value is KeyError:
            raise AttributeError(name)
        if isinstance(value, Exception):
            raise value
        return value

    def __getattr__(self, name):
        if name.startswith("private_get_"):
            return lambda: self._answer(name)
        raise AttributeError(name)

    def fetch_balance(self):
        return {"total": {"USDT": 100.0}}


def kucoin_with(responses):
    provider = KuCoinProvider(Credentials(key="k", secret="s", password="p"))
    provider._exchange = FakeKuCoinExchange(responses)
    return provider


def test_kucoin_reads_permissions_from_the_api_key_endpoint():
    provider = kucoin_with(
        {"private_get_user_api_key": {"data": [{"permission": "General,Trade"}]}}
    )
    assert provider.permissions() == {"general", "trade"}


def test_kucoin_accepts_a_list_valued_permission_field():
    provider = kucoin_with(
        {"private_get_user_api_key": {"data": {"permissions": ["General", "Trade"]}}}
    )
    assert provider.permissions() == {"general", "trade"}


def test_kucoin_refuses_to_call_an_empty_answer_read_only():
    # The account-summary endpoint carries no permission field. Reading it and
    # returning {"read"} made an unanswerable question look like a definite
    # read-only key -- and made the withdrawal check pass on no evidence.
    provider = kucoin_with({"private_get_user_info": {"data": {"level": 0}}})
    with pytest.raises(ProviderError):
        provider.permissions()


def test_kucoin_falls_through_to_the_older_endpoint():
    provider = kucoin_with({
        "private_get_user_api_key": RuntimeError("404 not found"),
        "private_get_user_info": {"data": [{"permission": "Trade"}]},
    })
    assert provider.permissions() == {"trade"}


def test_a_venue_that_cannot_report_permissions_says_so():
    provider = CcxtProvider("binance", Credentials(key="k", secret="s"))
    provider._exchange = FakeKuCoinExchange({})
    with pytest.raises(ProviderError):
        provider.permissions()


def test_an_unreadable_permission_list_never_clears_the_withdrawal_check():
    """The important half: no evidence must not become a passing security check."""

    class Unintrospectable(FakeProvider):
        def permissions(self):
            raise ProviderError("this venue does not report key permissions")

    outcome = engine.run_suite("connectivity", Unintrospectable())
    perm = next(r for r in outcome.results if r.code == "provider.permissions")
    assert perm.status == C.SKIPPED
    assert perm.status != C.PASSED
    assert perm.severity == C.WARN_SEV, "an unrun security check must not look like info"
    assert "withdraw" in (perm.remediation or "")


# ---------------------------------------------------------------------------
# How far back a venue's candles go
# ---------------------------------------------------------------------------

class CandleExchange:
    """A venue whose history starts on a given day.

    `answers_since_zero` distinguishes the two behaviours that matter: some
    venues answer an ancient `since` with their oldest candle, others answer
    with nothing at all -- which looks exactly like "no such pair" unless you
    go looking for the boundary.
    """

    def __init__(self, starts, *, answers_since_zero=True, symbols=("BTC/USDT",)):
        self.starts_ms = int(starts.timestamp() * 1000)
        self.answers_since_zero = answers_since_zero
        self.markets = {s: {} for s in symbols}
        self.calls = 0

    def load_markets(self):
        return self.markets

    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None):
        self.calls += 1
        if symbol not in self.markets:
            raise ValueError("no market")
        if since is not None and since < self.starts_ms:
            if not self.answers_since_zero:
                return []
            return [[self.starts_ms, 1, 2, 0.5, 1.5, 100]]
        return [[max(since or self.starts_ms, self.starts_ms), 1, 2, 0.5, 1.5, 100]]

    def close(self):
        pass


def _provider_with(exchange):
    provider = CcxtProvider("kucoin", Credentials())
    provider._exchange = exchange
    return provider


def test_earliest_candle_when_the_venue_answers_an_ancient_since():
    start = datetime(2017, 9, 15, tzinfo=timezone.utc)
    found = _provider_with(CandleExchange(start)).earliest_candle("BTC/USDT")
    assert found.date() == start.date()


def test_earliest_candle_is_found_by_search_when_the_venue_returns_nothing():
    # The case that silently produced no answer before.
    start = datetime(2021, 2, 4, tzinfo=timezone.utc)
    exchange = CandleExchange(start, answers_since_zero=False)
    found = _provider_with(exchange).earliest_candle("BTC/USDT")
    assert abs((found - start).total_seconds()) <= 86400
    assert exchange.calls < 40, "the search should converge, not sweep"


def test_a_pair_the_venue_does_not_list_is_refused_clearly():
    exchange = CandleExchange(datetime(2020, 1, 1, tzinfo=timezone.utc))
    with pytest.raises(ProviderError, match="does not list"):
        _provider_with(exchange).earliest_candle("NOPE/USDT")


# ---------------------------------------------------------------------------
# "Not in the listing" is not "not at the venue"
# ---------------------------------------------------------------------------

def test_an_order_missing_from_the_listing_is_confirmed_before_being_reported():
    """The false alarm this raised on the first real live trade.

    KuCoin implements no fetchOrders at all and its closed-order listing is
    window-limited, so an order placed minutes ago can be absent from a
    thirty-day query. Reporting that as "the bot recorded an order the exchange
    does not have" accuses the bot of inventing a trade that plainly happened --
    and a verification that cries wolf on every live order gets ignored, which
    is worse than not running it.
    """
    from app.providers.base import OrderInfo
    from app.validation.reconcile import reconcile_orders

    real = OrderInfo(order_id="abc123", symbol="XMR/USDT", side="buy", status="closed",
                     order_type="limit", price=417.51, average=417.51, amount=0.567,
                     filled=0.567, remaining=0.0, cost=236.72817, fee_cost=None,
                     fee_currency=None, timestamp=None)

    class Provider:
        name = "kucoin"
        looked_up = []

        def fetch_orders(self, symbol, *, since=None, limit=100):
            return []                      # the windowed listing misses it

        def fetch_order(self, order_id, symbol):
            self.looked_up.append((order_id, symbol))
            return real if order_id == "abc123" else None

    provider = Provider()
    findings = reconcile_orders(provider, [{
        "pair": "XMR/USDT", "ft_order_id": "15", "exchange_order_id": "abc123",
        "status": "closed", "side": "buy", "price": 417.51, "average": 417.51,
        "amount": 0.567, "filled": 0.567, "cost": 236.72817,
    }])
    assert provider.looked_up == [("abc123", "XMR/USDT")], "it never asked the venue directly"
    kinds = {f.kind for f in findings}
    assert "missing_on_exchange" not in kinds, f"real order reported as missing: {kinds}"


def test_an_order_the_venue_really_does_not_have_is_still_reported():
    """The direct lookup must not turn the check into a rubber stamp."""
    from app.validation.reconcile import reconcile_orders

    class Provider:
        name = "kucoin"

        def fetch_orders(self, symbol, *, since=None, limit=100):
            return []

        def fetch_order(self, order_id, symbol):
            return None

    findings = reconcile_orders(Provider(), [{
        "pair": "XMR/USDT", "ft_order_id": "15", "exchange_order_id": "ghost",
        "status": "closed", "side": "buy",
    }])
    assert any(f.kind == "missing_on_exchange" for f in findings)


def test_a_provider_that_cannot_look_one_up_still_reports_rather_than_crashing():
    from app.providers.base import ProviderError
    from app.validation.reconcile import reconcile_orders

    class Provider:
        name = "paper"

        def fetch_orders(self, symbol, *, since=None, limit=100):
            return []

        def fetch_order(self, order_id, symbol):
            raise ProviderError("no single-order lookup here")

    findings = reconcile_orders(Provider(), [{
        "pair": "XMR/USDT", "ft_order_id": "15", "exchange_order_id": "abc123",
    }])
    assert any(f.kind == "missing_on_exchange" for f in findings)
