"""The start / stop / pause / reload chain, end to end through the app.

Reading the code proves the paths are spelled right; it does not prove the
request that leaves the app is a POST to the endpoint freqtrade actually
publishes, nor that the router hands the answer back in the shape the page
reads. Both have broken before -- a GET where a POST belonged would come back
405, and the page would show "the bot returned 405" for a button that looks
fine in the source.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.api.routers import live
from app.bot_api.client import BotClient, BotError

# The four freqtrade publishes as POST under /api/v1/. stopentry is the alias
# freqtrade keeps for /pause; both are mounted on the same handler there.
CONTROLS = ["start", "stop", "stopentry", "reload_config"]


class Transport(httpx.BaseTransport):
    def __init__(self, status=200, body=None):
        self.status, self.body = status, body if body is not None else {"status": "ok"}
        self.seen: list[tuple[str, str, bytes]] = []

    def handle_request(self, request):
        self.seen.append((request.method, request.url.path, request.content))
        return httpx.Response(self.status, json=self.body, request=request)


@pytest.fixture
def bot(monkeypatch):
    def make(status=200, body=None):
        transport = Transport(status, body)
        real = httpx.Client

        def patched(*args, **kwargs):
            kwargs["transport"] = transport
            return real(*args, **kwargs)

        monkeypatch.setattr(httpx, "Client", patched)
        client = BotClient("http://freqtrade-bot:8080", "u", "p")
        client.transport = transport
        return client

    return make


class DB:
    """The caller's RLS-scoped client, with one bot they own."""

    def select(self, table, **kwargs):
        return [{"id": "b1", "name": "mine", "api_base_url": "http://freqtrade-bot:8080"}]


class User:
    profile_id = "p1"


# ---------------------------------------------------------------------------
# What actually goes over the wire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", CONTROLS)
def test_each_control_posts_to_the_endpoint_freqtrade_publishes(bot, action):
    client = bot(body={"status": "stopping trader ..."})
    result = client.act(action)
    method, path, _ = client.transport.seen[0]
    assert (method, path) == ("POST", f"/api/v1/{action}"), (
        "freqtrade publishes these as POST; a GET comes back 405 and the page "
        "reports it as a bot error"
    )
    assert result == {"status": "stopping trader ..."}


@pytest.mark.parametrize("action", CONTROLS)
def test_the_router_passes_the_action_through_and_keeps_the_answer(bot, action):
    client = bot(body={"status": f"{action} accepted"})
    body = live.ControlRequest(action=action)
    out = asyncio.run(live.control(body, User(), DB()))
    # The page reads r.result.status; anything else silently shows "start sent".
    assert out["result"]["status"] == f"{action} accepted"
    assert client.transport.seen[0][1] == f"/api/v1/{action}"


# ---------------------------------------------------------------------------
# What must not go over the wire
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("action", ["forceenter", "forcebuy", "delete_lock",
                                    "stop; start", "../forceenter", ""])
def test_only_the_four_controls_are_accepted(action):
    """The pattern on ControlRequest is the boundary, not a hint."""
    with pytest.raises(ValidationError):
        live.ControlRequest(action=action)


def test_a_caller_without_their_own_bot_cannot_stop_anyone(bot):
    bot()

    class NoBots:
        def select(self, table, **kwargs):
            return []

    with pytest.raises(HTTPException) as exc:
        asyncio.run(live.control(live.ControlRequest(action="stop"), User(), NoBots()))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# When the bot says no
# ---------------------------------------------------------------------------

def test_an_unreachable_bot_is_a_503_not_a_502(bot, monkeypatch):
    """A deploy in progress is not the same failure as a bot that rejected you."""
    real = httpx.Client

    def patched(*args, **kwargs):
        kwargs["transport"] = _Boom()
        return real(*args, **kwargs)

    class _Boom(httpx.BaseTransport):
        def handle_request(self, request):
            raise httpx.ConnectError("no route to host")

    monkeypatch.setattr(httpx, "Client", patched)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(live.control(live.ControlRequest(action="stop"), User(), DB()))
    assert exc.value.status_code == 503


def test_a_rejected_login_names_the_setting_to_fix(bot):
    client = bot(status=401)
    with pytest.raises(BotError, match="API_USERNAME"):
        client.act("stop")


def test_the_control_list_matches_what_the_client_permits():
    """Two lists that must not drift: the request pattern and the allowlist."""
    import re

    pattern = live.ControlRequest.model_fields["action"].metadata[0].pattern
    accepted = set(re.fullmatch(r"\^\((.*)\)\$", pattern).group(1).split("|"))
    assert accepted == set(CONTROLS)
    assert accepted <= BotClient.ACTIONS, "the router would ask for an unpermitted action"
    assert BotClient.ACTIONS - accepted == {"forceexit"}, (
        "forceexit has its own endpoint and its own confirmation; anything else "
        "appearing here is an action with no route to reach it"
    )
