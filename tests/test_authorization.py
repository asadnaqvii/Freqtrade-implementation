"""Tests for who may reach what.

Written after a real hole: the live-trading endpoints were added with only an
authentication check. Any account that could sign up -- and the Supabase project
accepted open registration -- could read the owner's positions, wallet and logs,
and call force-exit to close their positions. RLS protected every other table;
these routes bypassed it by reading the bot's address from process settings
instead of from the caller's own rows.

Authentication is not authorization. These tests hold that line.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.api.routers import live


class DB:
    """Stands in for the caller's RLS-scoped client."""

    def __init__(self, rows=None, boom=None):
        self.rows = rows if rows is not None else []
        self.boom = boom
        self.queries = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None,
               offset=None):
        self.queries.append((table, dict(filters or {})))
        if self.boom:
            raise self.boom
        return self.rows


def test_a_caller_with_no_bot_of_their_own_is_refused():
    # The exact scenario: a stranger signs up and opens the Live tab.
    with pytest.raises(HTTPException) as exc:
        live._client_for_caller(DB(rows=[]))
    assert exc.value.status_code == 404


def test_the_bot_address_comes_from_the_callers_rows_not_from_settings(monkeypatch):
    monkeypatch.setenv("FREQTRADE_API_BASE_URL", "http://someone-elses-bot:8080")
    from app.core import config

    config.get_settings.cache_clear()
    db = DB(rows=[{"id": "b1", "name": "mine", "api_base_url": "http://my-bot:8080"}])
    client = live._client_for_caller(db)
    assert client.base_url == "http://my-bot:8080"
    config.get_settings.cache_clear()


def test_the_lookup_goes_through_the_callers_own_client():
    db = DB(rows=[{"id": "b1", "api_base_url": "http://my-bot:8080"}])
    live._client_for_caller(db)
    table, _ = db.queries[0]
    assert table == "bot_instances", "ownership must be decided by RLS, not by code"


def test_a_failed_lookup_denies_rather_than_falls_back():
    # The dangerous shape: an error path that quietly reverts to the shared bot.
    with pytest.raises(HTTPException) as exc:
        live._client_for_caller(DB(boom=RuntimeError("postgrest down")))
    assert exc.value.status_code in (404, 502)


def test_someone_elses_bot_is_indistinguishable_from_no_bot():
    # Returning 403 for "exists but not yours" would confirm the bot exists.
    with pytest.raises(HTTPException) as exc:
        live._client_for_caller(DB(rows=[]))
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# The allowlist
# ---------------------------------------------------------------------------

def principal(email):
    from app.core.security import Principal

    return Principal(profile_id="p1", email=email, role="authenticated",
                     token="t", claims={})


@pytest.fixture(autouse=True)
def clear_settings():
    from app.core import config

    config.get_settings.cache_clear()
    yield
    config.get_settings.cache_clear()


def test_an_unlisted_email_is_refused_even_with_a_valid_token(monkeypatch):
    from app.api import deps
    from app.core import config

    monkeypatch.setenv("ALLOWED_EMAILS", "owner@example.com")
    config.get_settings.cache_clear()
    monkeypatch.setattr(deps, "principal_from_token", lambda t: principal("stranger@example.com"))
    monkeypatch.setattr(deps, "bearer_token", lambda h: "token")

    with pytest.raises(HTTPException) as exc:
        deps.current_principal("Bearer token")
    assert exc.value.status_code == 401


def test_a_listed_email_gets_through(monkeypatch):
    from app.api import deps
    from app.core import config

    monkeypatch.setenv("ALLOWED_EMAILS", "owner@example.com, other@example.com")
    config.get_settings.cache_clear()
    monkeypatch.setattr(deps, "principal_from_token", lambda t: principal("Owner@Example.com"))
    monkeypatch.setattr(deps, "bearer_token", lambda h: "token")

    assert deps.current_principal("Bearer token").profile_id == "p1"


def test_an_unset_allowlist_leaves_rls_in_charge(monkeypatch):
    from app.api import deps
    from app.core import config

    monkeypatch.delenv("ALLOWED_EMAILS", raising=False)
    config.get_settings.cache_clear()
    monkeypatch.setattr(deps, "principal_from_token", lambda t: principal("anyone@example.com"))
    monkeypatch.setattr(deps, "bearer_token", lambda h: "token")

    assert deps.current_principal("Bearer token").email == "anyone@example.com"


def test_a_token_with_no_email_cannot_slip_past_a_set_allowlist(monkeypatch):
    from app.api import deps
    from app.core import config

    monkeypatch.setenv("ALLOWED_EMAILS", "owner@example.com")
    config.get_settings.cache_clear()
    monkeypatch.setattr(deps, "principal_from_token", lambda t: principal(None))
    monkeypatch.setattr(deps, "bearer_token", lambda h: "token")

    with pytest.raises(HTTPException) as exc:
        deps.current_principal("Bearer token")
    assert exc.value.status_code == 401
