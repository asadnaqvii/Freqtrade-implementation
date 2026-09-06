"""Tests for configuration handling.

with_search_path is the piece that keeps freqtrade's tables out of the schema
Supabase exposes through PostgREST, so it gets the most attention here.
"""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, normalise_db_url, with_search_path
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


# ---------------------------------------------------------------------------
# The search path belongs on the role, not in the URL
# ---------------------------------------------------------------------------

def test_normalise_pins_the_driver_without_touching_the_query():
    url = normalise_db_url("postgresql://u:p@h:5432/db")
    assert url.startswith("postgresql+psycopg2://")
    # No options parameter: through a pooler it does not survive, and the bot
    # then writes to whatever schema the mangled value names.
    assert "options" not in url
    assert "search_path" not in url


def test_normalise_leaves_sqlite_alone():
    assert normalise_db_url("sqlite:///x.sqlite") == "sqlite:///x.sqlite"


def test_freqtrade_url_omits_search_path_by_default(monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.delenv("FREQTRADE_DB_SEARCH_PATH_IN_URL", raising=False)
    get_settings.cache_clear()
    url = get_settings().freqtrade_db_url
    get_settings.cache_clear()
    assert "search_path" not in url


def test_search_path_can_still_be_forced_into_the_url(monkeypatch):
    """Still correct for a direct connection, which is why it remains available."""
    from app.core.config import get_settings

    monkeypatch.setenv("SUPABASE_DB_URL", "postgresql://u:p@h:5432/db")
    monkeypatch.setenv("FREQTRADE_DB_SEARCH_PATH_IN_URL", "true")
    monkeypatch.setenv("FREQTRADE_DB_SCHEMA", "ft_main")
    get_settings.cache_clear()
    url = get_settings().freqtrade_db_url
    get_settings.cache_clear()
    assert "search_path" in url


# ---------------------------------------------------------------------------
# Keepalives, and not breaking the search path while adding them
# ---------------------------------------------------------------------------

def test_a_postgres_url_gets_tcp_keepalives():
    """Supabase's pooler drops connections and freqtrade treats the next failed
    query as fatal. Keepalives make the kernel prove the socket is alive on a
    schedule instead of discovering it is dead mid-session."""
    url = normalise_db_url("postgresql://u:p@host:5432/postgres")
    for part in ("keepalives=1", "keepalives_idle=30",
                 "keepalives_interval=10", "keepalives_count=5"):
        assert part in url, part


def test_adding_keepalives_does_not_re_encode_the_search_path():
    """The bug this nearly shipped.

    Round-tripping the query through urlencode rewrites `%20` as `+`, and libpq
    does not read `+` as a space -- that is an HTML form convention, not RFC
    3986. The option would arrive as `-c+search_path=...`, the server would
    reject it, and freqtrade's tables would land in whatever schema it fell
    back to.
    """
    url = with_search_path("postgresql://u:p@host:5432/postgres", "ft_main")
    assert "options=-c%20search_path%3Dft_main%2Cpublic" in url
    assert "+search_path" not in url
    assert "keepalives=1" in url


def test_an_explicit_keepalive_setting_is_not_overridden():
    url = normalise_db_url("postgresql://u:p@h/db?keepalives_idle=99")
    assert "keepalives_idle=99" in url
    assert "keepalives_idle=30" not in url


def test_sqlite_is_left_alone():
    """Keepalives are a TCP concept; a file has no socket to keep alive."""
    assert normalise_db_url("sqlite:///user_data/tradesv3.sqlite") == \
        "sqlite:///user_data/tradesv3.sqlite"
