"""Tests for the app's link to the trading bot.

The app now holds the bot's API login, which is what makes live positions and a
force-exit button possible. That is a real widening of what a compromise of the
public service could reach, and the allowlist in BotClient is the thing keeping
it bounded. These tests are that boundary's teeth: if someone later calls an
endpoint that is not listed, the suite says so rather than the exchange.
"""

from __future__ import annotations

import httpx
import pytest

from app.bot_api.client import BotClient, BotError, BotNotConfigured, BotUnreachable


class Transport(httpx.BaseTransport):
    """Answers requests from a table instead of over the network."""

    def __init__(self, replies=None, boom=None):
        self.replies = replies or {}
        self.boom = boom
        self.seen: list[tuple[str, str]] = []

    def handle_request(self, request):
        self.seen.append((request.method, request.url.path))
        if self.boom:
            raise self.boom
        status, body = self.replies.get(request.url.path, (200, {}))
        return httpx.Response(status, json=body, request=request)


@pytest.fixture
def client(monkeypatch):
    def make(replies=None, boom=None):
        transport = Transport(replies, boom)
        real = httpx.Client

        def patched(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", patched)
        bot = BotClient("http://freqtrade-bot:8080", "u", "p")
        bot.transport = transport
        return bot

    return make


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

def test_an_unlisted_read_is_refused_before_any_request(client):
    bot = client()
    with pytest.raises(BotError, match="not a permitted read"):
        bot.get("sysinfo/../start")
    assert bot.transport.seen == [], "it must refuse without touching the network"


@pytest.mark.parametrize("path", ["start", "stop", "forceenter", "forcebuy", "reload_config"])
def test_dangerous_endpoints_are_not_reachable(client, path):
    bot = client()
    with pytest.raises(BotError):
        bot.get(path)
    with pytest.raises(BotError):
        bot.act(path)
    assert bot.transport.seen == []


def test_forceenter_is_deliberately_absent():
    # Closing a position you hold is a dashboard control. Opening one from a web
    # page is a different kind of power, and entries are the strategy's job.
    assert "forceenter" not in BotClient.ACTIONS
    assert "forceenter" not in BotClient.READS
    assert BotClient.ACTIONS == {"forceexit"}


def test_reads_and_actions_do_not_overlap():
    assert not (BotClient.READS & BotClient.ACTIONS)


def test_a_permitted_read_goes_through(client):
    bot = client({"/api/v1/status": (200, [{"trade_id": 1, "pair": "BTC/USDT"}])})
    assert bot.get("status")[0]["pair"] == "BTC/USDT"
    assert bot.transport.seen == [("GET", "/api/v1/status")]


def test_force_exit_posts_the_trade_id(client):
    bot = client({"/api/v1/forceexit": (200, {"result": "Created exit order"})})
    assert bot.force_exit("7", order_type="market")["result"] == "Created exit order"
    assert bot.transport.seen == [("POST", "/api/v1/forceexit")]


# ---------------------------------------------------------------------------
# Failure modes the UI distinguishes
# ---------------------------------------------------------------------------

def test_an_unreachable_bot_is_its_own_error(client):
    bot = client(boom=httpx.ConnectError("no route to host"))
    with pytest.raises(BotUnreachable):
        bot.get("status")


def test_a_rejected_login_says_which_setting_is_wrong(client):
    bot = client({"/api/v1/status": (401, {})})
    with pytest.raises(BotError, match="API_USERNAME"):
        bot.get("status")


def test_missing_configuration_is_not_an_unreachable_bot(monkeypatch):
    from app.core import config as cfg

    monkeypatch.delenv("FREQTRADE_API_BASE_URL", raising=False)
    cfg.get_settings.cache_clear()
    with pytest.raises(BotNotConfigured):
        BotClient.from_settings()
    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Partial failure
# ---------------------------------------------------------------------------

def test_overview_reports_what_failed_instead_of_failing_whole(client):
    # A bot that cannot reach the exchange should still be able to tell you what
    # it holds; one dead endpoint must not blank the page.
    bot = client({
        "/api/v1/show_config": (200, {"state": "running", "dry_run": True}),
        "/api/v1/status": (200, [{"trade_id": 1}]),
        "/api/v1/balance": (500, {"detail": "exchange timeout"}),
    })
    out = bot.overview()
    assert out["config"]["state"] == "running"
    assert out["status"] == [{"trade_id": 1}]
    assert out["balance"] is None
    assert "balance" in out["errors"]
    assert "profit" in out and "daily" in out


def test_overview_asks_for_every_panel_the_page_draws(client):
    bot = client()
    bot.overview()
    asked = {path for _, path in bot.transport.seen}
    for endpoint in ("show_config", "status", "profit", "daily", "balance",
                     "count", "performance"):
        assert f"/api/v1/{endpoint}" in asked
