"""Tests for the PostgREST client.

The 300 test is the one that matters. PostgREST answers 300 Multiple Choices
when an embedded resource is ambiguous, with an error document as the body. A
client that only treats 4xx as failure hands that document back as if it were
data, and the caller indexes an error dict as a row -- which is exactly how this
surfaced in production, as `KeyError: 0` several layers away from the cause.
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.core.supabase import SupabaseClient, SupabaseError


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_ANON_KEY", "anon")
    monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service")
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def client_with(monkeypatch, status, body):
    def fake_request(self, method, url, **kwargs):
        content = body if isinstance(body, (str, bytes)) else json.dumps(body)
        return httpx.Response(status, text=content,
                              request=httpx.Request(method, url))

    monkeypatch.setattr(httpx.Client, "request", fake_request)
    return SupabaseClient("token")


AMBIGUOUS = {
    "code": "PGRST201",
    "message": "Could not embed because more than one relationship was found",
    "details": [
        {"cardinality": "one-to-many", "relationship": "strategy_specs_current_version_fkey"},
        {"cardinality": "many-to-one", "relationship": "strategy_versions_strategy_id_fkey"},
    ],
}


def test_300_ambiguous_embed_raises_instead_of_returning_the_error_body(monkeypatch):
    db = client_with(monkeypatch, 300, AMBIGUOUS)
    with pytest.raises(SupabaseError) as exc:
        db.select("strategy_versions")
    assert exc.value.status == 300
    # The message must name the actual fix, not just the status code.
    assert "more than one foreign key" in (exc.value.hint or "")


def test_select_one_does_not_index_an_error_document(monkeypatch):
    """The regression: this used to raise KeyError: 0 far from the real cause."""
    db = client_with(monkeypatch, 300, AMBIGUOUS)
    with pytest.raises(SupabaseError):
        db.select_one("strategy_versions")


def test_a_bare_object_from_a_select_is_rejected(monkeypatch):
    db = client_with(monkeypatch, 200, {"id": "not-a-list"})
    with pytest.raises(SupabaseError) as exc:
        db.select("strategy_versions")
    assert "expected a list of rows" in (exc.value.hint or "")


def test_normal_select_returns_rows(monkeypatch):
    db = client_with(monkeypatch, 200, [{"id": "a"}, {"id": "b"}])
    assert db.select("strategy_specs") == [{"id": "a"}, {"id": "b"}]


def test_select_one_returns_the_first_row(monkeypatch):
    db = client_with(monkeypatch, 200, [{"id": "a"}])
    assert db.select_one("strategy_specs") == {"id": "a"}


def test_select_one_returns_none_when_empty(monkeypatch):
    db = client_with(monkeypatch, 200, [])
    assert db.select_one("strategy_specs") is None


def test_rls_refusal_carries_a_useful_hint(monkeypatch):
    db = client_with(monkeypatch, 403, {"message": "permission denied"})
    with pytest.raises(SupabaseError) as exc:
        db.select("strategy_specs")
    assert "RLS" in (exc.value.hint or "")


def test_unfiltered_update_is_refused():
    from app.core.config import get_settings

    get_settings.cache_clear()
    db = SupabaseClient("token")
    # PostgREST would happily rewrite the whole table.
    with pytest.raises(ValueError, match="refusing an unfiltered write"):
        db.update("trade_archive", {"is_open": False}, filters={})


def test_unfiltered_delete_is_refused():
    db = SupabaseClient("token")
    with pytest.raises(ValueError, match="refusing an unfiltered delete"):
        db.delete("trade_archive", filters={})
