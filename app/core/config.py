"""Runtime configuration, read once from the environment.

Every deployable in this repo -- bot, API and worker -- imports this module so
there is a single place that knows what an env var is called and what happens
when it is missing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from urllib.parse import quote, urlparse, urlunparse

# Schema name pattern, mirrored from the check in refresh_freqtrade_views().
# Anything reaching a search_path or a format() must match this.
SCHEMA_RE = r"^[a-z_][a-z0-9_]{0,62}$"


class ConfigError(RuntimeError):
    """Raised when configuration is missing or self-contradictory."""


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name, default)
    if value is not None:
        value = value.strip()
    return value or None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from exc


@dataclass(frozen=True)
class SupabaseConfig:
    """How to reach Supabase, both as an API and as a plain Postgres."""

    url: str | None
    service_role_key: str | None
    anon_key: str | None
    jwt_secret: str | None
    db_url: str | None

    @property
    def configured(self) -> bool:
        return bool(self.url and self.service_role_key)

    @property
    def rest_url(self) -> str:
        if not self.url:
            raise ConfigError("SUPABASE_URL is not set")
        return self.url.rstrip("/") + "/rest/v1"

    def require_service_key(self) -> str:
        if not self.service_role_key:
            raise ConfigError(
                "SUPABASE_SERVICE_ROLE_KEY is not set. The API and worker need it to "
                "write on behalf of a user; it must never be sent to a browser."
            )
        return self.service_role_key


@dataclass(frozen=True)
class BotConfig:
    """Identity and wiring of the live trading process."""

    name: str
    exchange: str
    strategy: str
    db_schema: str
    dry_run: bool
    stake_currency: str
    stake_amount: float
    max_open_trades: int
    api_port: int
    api_username: str | None
    api_password: str | None
    # Where the bot's REST API answers on the private network. Set for the app
    # service so it can reach a bot it cannot see from the internet.
    api_base_url: str | None
    deploy_target: str
    environment: str
    # Whether to pin the schema via the connection URL. Off by default: through
    # a pooler the role default is the only form that survives.
    search_path_in_url: bool

    def __post_init__(self) -> None:
        import re

        if not re.match(SCHEMA_RE, self.db_schema):
            raise ConfigError(
                f"FREQTRADE_DB_SCHEMA must match {SCHEMA_RE}, got {self.db_schema!r}"
            )


@dataclass(frozen=True)
class WorkerConfig:
    """Backtest worker knobs."""

    name: str
    data_dir: str
    user_dir: str
    poll_interval_seconds: int
    heartbeat_seconds: int
    job_timeout_seconds: int
    max_download_days: int


@dataclass(frozen=True)
class Settings:
    supabase: SupabaseConfig
    bot: BotConfig
    worker: WorkerConfig
    cors_origins: list[str] = field(default_factory=list)
    log_level: str = "INFO"

    @property
    def freqtrade_db_url(self) -> str | None:
        """The SQLAlchemy URL freqtrade should use.

        Returns None when no Postgres is configured, which is the signal to fall
        back to freqtrade's own SQLite default so local development and the
        existing deployment keep working untouched.

        The search path is NOT put in the URL by default. Passing it as an
        `options=-c search_path=...` startup parameter works against a direct
        Postgres connection but does not survive Supabase's pooler: the bot
        connected fine and then failed every unqualified `INSERT INTO trades`
        with a schema name that was never configured. The reliable place for it
        is a server-side role default:

            alter role ft_bot set search_path = ft_main, public;

        Set FREQTRADE_DB_SEARCH_PATH_IN_URL=true to go back to the URL form,
        which is still correct when connecting directly rather than through a
        pooler.
        """
        base = self.supabase.db_url
        if not base:
            return None
        if self.bot.search_path_in_url:
            return with_search_path(base, self.bot.db_schema)
        return normalise_db_url(base)


def normalise_db_url(db_url: str) -> str:
    """Force an explicit driver so SQLAlchemy cannot pick a different one.

    A bare `postgresql://` lets SQLAlchemy choose whatever DBAPI it finds. Being
    explicit means a missing psycopg2 fails loudly at startup rather than
    silently binding to something else.
    """
    parts = urlparse(db_url)
    if not parts.scheme.startswith("postgres"):
        return db_url
    scheme = parts.scheme
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+psycopg2"
    return urlunparse(parts._replace(scheme=scheme))


def with_search_path(db_url: str, schema: str) -> str:
    """Attach `options=-c search_path=<schema>,public` to a Postgres URL.

    This is what keeps freqtrade's tables out of `public`, and therefore out of
    the PostgREST API surface, without patching freqtrade.
    """
    import re

    if not re.match(SCHEMA_RE, schema):
        raise ConfigError(f"refusing to build a db url with schema {schema!r}")

    parts = urlparse(db_url)
    if not parts.scheme.startswith("postgres"):
        # sqlite and friends have no schemas; hand the url back untouched.
        return db_url

    # freqtrade hands the url to SQLAlchemy, which needs an explicit driver.
    scheme = parts.scheme
    if scheme in {"postgres", "postgresql"}:
        scheme = "postgresql+psycopg2"

    options = f"-c search_path={schema},public"
    query = parts.query
    if "options=" in query:
        # Caller already pinned a search_path; respect it rather than fighting.
        return urlunparse(parts._replace(scheme=scheme))

    encoded = f"options={quote(options, safe='')}"
    query = f"{query}&{encoded}" if query else encoded
    return urlunparse(parts._replace(scheme=scheme, query=query))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    supabase = SupabaseConfig(
        url=_env("SUPABASE_URL"),
        service_role_key=_env("SUPABASE_SERVICE_ROLE_KEY"),
        anon_key=_env("SUPABASE_ANON_KEY") or _env("SUPABASE_PUBLISHABLE_KEY"),
        jwt_secret=_env("SUPABASE_JWT_SECRET"),
        # Accept the freqtrade-style name too, since the bot's env already uses
        # that prefix for everything else it reads.
        db_url=_env("SUPABASE_DB_URL") or _env("FREQTRADE__DB_URL") or _env("DATABASE_URL"),
    )

    bot = BotConfig(
        name=_env("BOT_NAME", "freqtrade-bot") or "freqtrade-bot",
        exchange=_env("FREQTRADE__EXCHANGE__NAME", "kucoin") or "kucoin",
        strategy=_env("FREQTRADE_STRATEGY", "TrendPullbackStrategy") or "TrendPullbackStrategy",
        db_schema=_env("FREQTRADE_DB_SCHEMA", "ft_main") or "ft_main",
        dry_run=_env_bool("DRY_RUN", False),
        stake_currency=_env("FREQTRADE_STAKE_CURRENCY", "USDT") or "USDT",
        stake_amount=float(_env("FREQTRADE_STAKE_AMOUNT", "10") or 10),
        max_open_trades=_env_int("FREQTRADE_MAX_OPEN_TRADES", 6),
        api_port=_env_int("PORT", 8080),
        api_username=_env("API_USERNAME"),
        api_password=_env("API_PASSWORD"),
        api_base_url=_env("FREQTRADE_API_BASE_URL"),
        deploy_target=_env("DEPLOY_TARGET", "render") or "render",
        environment=_env("ENVIRONMENT", "production") or "production",
        search_path_in_url=_env_bool("FREQTRADE_DB_SEARCH_PATH_IN_URL", False),
    )

    worker = WorkerConfig(
        name=_env("WORKER_NAME", f"worker-{os.getpid()}") or f"worker-{os.getpid()}",
        data_dir=_env("BACKTEST_DATA_DIR", "/data/candles") or "/data/candles",
        user_dir=_env("BACKTEST_USER_DIR", "/data/user_data") or "/data/user_data",
        poll_interval_seconds=_env_int("WORKER_POLL_SECONDS", 10),
        heartbeat_seconds=_env_int("WORKER_HEARTBEAT_SECONDS", 30),
        job_timeout_seconds=_env_int("WORKER_JOB_TIMEOUT_SECONDS", 3600),
        max_download_days=_env_int("BACKTEST_MAX_DOWNLOAD_DAYS", 1825),
    )

    origins = _env("CORS_ORIGINS", "")
    cors = [o.strip() for o in (origins or "").split(",") if o.strip()]

    return Settings(
        supabase=supabase,
        bot=bot,
        worker=worker,
        cors_origins=cors,
        log_level=_env("LOG_LEVEL", "INFO") or "INFO",
    )
