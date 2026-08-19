"""Tests for configuration handling.

with_search_path is the piece that keeps freqtrade's tables out of the schema
Supabase exposes through PostgREST, so it gets the most attention here.
"""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, with_search_path
from app.core.security import fingerprint_secret, redact
from app.providers.credentials import CredentialError, _read


def test_search_path_is_appended_and_encoded():
    url = with_search_path("postgresql://u:p@host:5432/postgres", "ft_main")
    assert "options=-c%20search_path%3Dft_main%2Cpublic" in url


def test_driver_is_made_explicit_for_sqlalchemy():
    # A bare postgresql:// url makes SQLAlchemy pick a driver; be specific so the
    # bot fails loudly if psycopg2 is missing rather than picking something else.
    assert with_search_path("postgres://u:p@h/db", "ft_main").startswith("postgresql+psycopg2://")
    assert with_search_path("postgresql://u:p@h/db", "ft_main").startswith("postgresql+psycopg2://")


def test_an_existing_driver_is_left_alone():
    url = with_search_path("postgresql+psycopg://u:p@h/db", "ft_main")
    assert url.startswith("postgresql+psycopg://")


def test_existing_query_parameters_are_preserved():
    url = with_search_path("postgresql://u:p@h/db?sslmode=require", "ft_main")
    assert "sslmode=require" in url
    assert "search_path" in url


def test_a_caller_supplied_search_path_wins():
    url = with_search_path("postgresql://u:p@h/db?options=-csearch_path%3Dcustom", "ft_main")
    assert "custom" in url
    assert "ft_main" not in url


def test_sqlite_urls_pass_through_untouched():
    assert with_search_path("sqlite:///x.sqlite", "ft_main") == "sqlite:///x.sqlite"


@pytest.mark.parametrize("schema", [
    "public; drop table trades",
    "ft main",
    "FT_MAIN",
    "'; select 1 --",
    "x" * 80,
    "1bad",
])
def test_hostile_schema_names_are_refused(schema):
    # This value reaches a connection string and a format() inside a plpgsql
    # function, so it is checked in both places.
    with pytest.raises(ConfigError):
        with_search_path("postgresql://u:p@h/db", schema)


def test_fingerprint_is_a_sha256_hex_digest():
    digest = fingerprint_secret("some-api-key")
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)
    # The database column has a check constraint requiring exactly this shape.
    assert digest != fingerprint_secret("another-api-key")


def test_redact_keeps_only_the_tail():
    assert redact("supersecretkey") == "**********tkey"
    assert redact(None) == "<unset>"
    assert "supersecret" not in redact("supersecretkey")


def test_env_var_names_that_look_like_secrets_are_refused():
    # Someone pasting a key where a variable name belongs should get a clear
    # error, not a silently unresolved credential.
    with pytest.raises(CredentialError, match="not a valid environment variable name"):
        _read("68b9a1f4c2e7d9a03b5f1e8c7d2a4b60")


def test_proper_env_var_names_resolve(monkeypatch):
    monkeypatch.setenv("MY_TEST_KEY", "value")
    assert _read("MY_TEST_KEY") == "value"
    assert _read("MY_UNSET_KEY") is None
