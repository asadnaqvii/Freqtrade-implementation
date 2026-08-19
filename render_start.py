#!/usr/bin/env python3
"""Entry point for the freqtrade trading bot.

Two changes from a bare `freqtrade trade`:

  1. Persistence goes to Supabase Postgres instead of a SQLite file inside the
     container. On Render and Railway that filesystem is ephemeral, so every
     redeploy previously wiped the trade history the dashboard was showing.
     Falls back to SQLite when no database is configured, so local runs and the
     existing deployment keep working untouched.

  2. The bot registers itself in public.bot_instances and heartbeats. Since it
     runs as a private service with no public ingress, that row is how anything
     else knows it is alive.
"""

import json
import os
import secrets
import sys
import threading
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

print("=== freqtrade bot starting ===", flush=True)
print(f"Python: {sys.version.split()[0]}", flush=True)


# ---------------------------------------------------------------------------
# TA-Lib
# ---------------------------------------------------------------------------
# TA-Lib now ships prebuilt wheels, so the hand-rolled C build the old build.sh
# performed is no longer needed. Keep looking for a locally compiled copy anyway
# in case an older image is still around.
_ta_lib_candidates = [
    os.path.join(os.path.expanduser("~"), "ta-lib", "lib"),
    "/opt/render/project/ta-lib/lib",
]
_ld = os.environ.get("LD_LIBRARY_PATH", "")
for _path in _ta_lib_candidates:
    if os.path.exists(_path) and _path not in _ld:
        _ld = f"{_path}:{_ld}" if _ld else _path
if _ld:
    os.environ["LD_LIBRARY_PATH"] = _ld

try:
    import talib
    print(f"TA-Lib {talib.__version__} loaded", flush=True)
except ImportError as exc:
    print(f"WARNING: TA-Lib not importable: {exc}", flush=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
def _env(name, default=None):
    value = os.environ.get(name, default)
    return value.strip() if isinstance(value, str) else value


REQUIRED = ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET", "FREQTRADE__EXCHANGE__PASSWORD"]
missing = [v for v in REQUIRED if not _env(v)]
if missing:
    print(f"WARNING: missing {', '.join(missing)} -- cannot trade live", flush=True)

port = int(_env("PORT", "8080") or 8080)
strategy = _env("FREQTRADE_STRATEGY", "TrendPullbackStrategy")
exchange_name = _env("FREQTRADE__EXCHANGE__NAME", "kucoin")
db_schema = _env("FREQTRADE_DB_SCHEMA", "ft_main")
bot_name = _env("BOT_NAME", "freqtrade-bot")
dry_run = (_env("DRY_RUN", "false") or "false").lower() == "true"

config = {
    "max_open_trades": int(_env("FREQTRADE_MAX_OPEN_TRADES", "6") or 6),
    "stake_currency": _env("FREQTRADE_STAKE_CURRENCY", "USDT"),
    "stake_amount": float(_env("FREQTRADE_STAKE_AMOUNT", "10") or 10),
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": dry_run,
    "dry_run_wallet": 1000,
    "cancel_open_orders_on_exit": False,
    "unfilledtimeout": {"entry": 10, "exit": 10, "exit_timeout_count": 0, "unit": "minutes"},
    "entry_pricing": {
        "price_side": "same",
        "use_order_book": True,
        "order_book_top": 1,
        "price_last_balance": 0.0,
        "check_depth_of_market": {"enabled": False, "bids_to_ask_delta": 1},
    },
    "exit_pricing": {"price_side": "same", "use_order_book": True, "order_book_top": 1},
    "exchange": {
        "name": exchange_name,
        "key": _env("FREQTRADE__EXCHANGE__KEY", ""),
        "secret": _env("FREQTRADE__EXCHANGE__SECRET", ""),
        "password": _env("FREQTRADE__EXCHANGE__PASSWORD", ""),
        "ccxt_config": {},
        "ccxt_async_config": {"aiohttp_trust_env": True},
        # Populated at runtime by VolumePairList, not hardcoded.
        "pair_whitelist": [],
        "pair_blacklist": ["BNB/.*", ".*UP/USDT", ".*DOWN/USDT", ".*BEAR/USDT", ".*BULL/USDT"],
    },
    "pairlists": [
        {"method": "VolumePairList", "number_assets": 25, "sort_key": "quoteVolume",
         "min_value": 0, "refresh_period": 3600},
        {"method": "AgeFilter", "min_days_listed": 60},
        {"method": "SpreadFilter", "max_spread_ratio": 0.005},
        {"method": "RangeStabilityFilter", "lookback_days": 10, "min_rate_of_change": 0.03,
         "refresh_period": 3600},
        {"method": "VolatilityFilter", "lookback_days": 10, "min_volatility": 0.02,
         "max_volatility": 0.75, "refresh_period": 3600},
    ],
    "edge": {"enabled": False},
    "api_server": {
        "enabled": True,
        "listen_ip_address": "0.0.0.0",
        "listen_port": port,
        "verbosity": "error",
        "enable_openapi": True,
        # freqtrade enforces a minimum length here, so a short placeholder makes
        # the whole config invalid and the bot refuses to start. Generate a real
        # one when none is supplied rather than shipping a weak constant: this
        # signs the API's session tokens, and the API can place orders.
        "jwt_secret_key": _env("JWT_SECRET_KEY") or secrets.token_urlsafe(48),
        # This service has no public ingress, so the API is only reachable from
        # inside Render's private network. CORS is not the boundary here.
        "CORS_origins": [],
        "username": _env("API_USERNAME", "freqtrader"),
        "password": _env("API_PASSWORD", "freqtrader"),
    },
    "bot_name": bot_name,
    "initial_state": "running",
    "force_entry_enable": True,
    "internals": {"process_throttle_secs": 5},
    "strategy": strategy,
    # Hard backstop matching the strategy's design. The real exit logic is the
    # strategy's ATR-based custom_stoploss; a tighter config stoploss would
    # override it and stop every trade out almost immediately.
    "stoploss": float(_env("FREQTRADE_STOPLOSS", "-0.06") or -0.06),
}

os.makedirs("config", exist_ok=True)
with open("config/config.json", "w") as handle:
    json.dump(config, handle, indent=2)

print(f"strategy={strategy} exchange={exchange_name} dry_run={dry_run} port={port}", flush=True)
print(f"exchange key configured: {'yes' if config['exchange']['key'] else 'no'}", flush=True)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
db_url = None
try:
    from app.core.config import get_settings

    settings = get_settings()
    db_url = settings.freqtrade_db_url
except Exception as exc:
    print(f"WARNING: could not read platform settings ({exc}); using SQLite", flush=True)

def verify_database(url, expected_schema):
    """Connect once and confirm the session lands in the right schema.

    Freqtrade writes unqualified SQL -- `INSERT INTO trades` -- so the schema is
    decided entirely by search_path. Getting that wrong does not fail at
    startup; it fails on the first trade, which is a bad time to find out.
    Checking here turns a silent misconfiguration into a refusal to start.
    """
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed; cannot verify the database", flush=True)
        return False

    # psycopg2 wants a plain postgresql:// url, not SQLAlchemy's driver form.
    raw = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    try:
        conn = psycopg2.connect(raw, connect_timeout=20)
    except Exception as exc:
        detail = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)
        print(f"DATABASE ERROR: {detail}", flush=True)
        if "ENOTFOUND" in detail or "Tenant or user not found" in detail:
            print(
                "  The pooler does not recognise that user. Supabase's pooler expects "
                "<role>.<project-ref> as the username, and the host must be the pooler "
                "shown in Settings -> Database -> Connection string -> Session pooler.",
                flush=True,
            )
        elif "timeout" in detail.lower():
            print(
                "  Connection timed out. The direct host db.<ref>.supabase.co is "
                "IPv6-only and unreachable from Render; use the session pooler host.",
                flush=True,
            )
        return False

    try:
        cur = conn.cursor()
        cur.execute("show search_path")
        search_path = cur.fetchone()[0]
        cur.execute("select current_user, current_database()")
        user, database = cur.fetchone()
        print(f"database: connected as {user} to {database}", flush=True)
        print(f"database: search_path = {search_path}", flush=True)

        if expected_schema not in [p.strip().strip('\"') for p in search_path.split(",")]:
            print(
                f"DATABASE ERROR: search_path is {search_path!r} but freqtrade's tables "
                f"belong in {expected_schema!r}. Its SQL is unqualified, so it would "
                f"create and read tables in the wrong schema.\n"
                f"  Fix with:  alter role {user} set search_path = {expected_schema}, public;",
                flush=True,
            )
            return False

        cur.execute("select to_regclass(%s)", (f"{expected_schema}.trades",))
        existing = cur.fetchone()[0]
        print(
            f"database: {expected_schema}.trades "
            + ("found" if existing else "not created yet (freqtrade will create it)"),
            flush=True,
        )
        return True
    finally:
        conn.close()


if db_url:
    # Never print the URL; it carries the database password.
    print(f"persistence: postgres, schema {db_schema}", flush=True)
    if not verify_database(db_url, db_schema):
        print(
            "Refusing to start against a database that is not set up correctly. "
            "Trading with the wrong schema loses trade history silently.",
            flush=True,
        )
        sys.exit(1)
else:
    print("persistence: SQLite (ephemeral -- set SUPABASE_DB_URL to keep history)", flush=True)


#: How often the bot re-verifies its own credentials. Frequent enough that the
#: app's view is never far behind, rare enough to be invisible to rate limits.
SELFCHECK_INTERVAL_SECONDS = int(_env("SELFCHECK_INTERVAL_SECONDS", "900") or 900)


def _sole_profile_id(client):
    """The owner, when there is exactly one and PLATFORM_OWNER_ID was not set.

    Registering with a null owner is worse than not registering: RLS then hides
    the bot from the very dashboard meant to show it. Guessing is only safe when
    there is nothing to guess between, so more than one profile means give up
    and say so rather than pick.
    """
    try:
        profiles = client.select("profiles", columns="id", limit=2)
    except Exception as exc:
        print(f"could not resolve an owner: {exc}", flush=True)
        return None
    if len(profiles) == 1:
        print(f"PLATFORM_OWNER_ID unset; using the only profile {profiles[0]['id']}",
              flush=True)
        return profiles[0]["id"]
    print(f"PLATFORM_OWNER_ID unset and {len(profiles)} profiles exist; "
          "set it or this bot stays invisible to the dashboard", flush=True)
    return None


def _link_account(client, bot_id, owner_id):
    """Point this bot at the exchange_accounts row whose keys it is running with.

    The link is what lets the public app verify that account without holding a
    key: it asks this bot instead. Matching is on the venue and only when
    unambiguous -- a wrong link would have the app report one account's health
    for another.
    """
    try:
        accounts = client.select(
            "exchange_accounts",
            columns="id,label",
            filters={"owner_id": f"eq.{owner_id}", "provider": f"eq.{exchange_name}",
                     "is_active": "eq.true"},
            limit=3,
        )
        if len(accounts) != 1:
            print(f"not linking an account: {len(accounts)} active {exchange_name} "
                  "accounts for this owner", flush=True)
            return
        client.update("bot_instances", {"account_id": accounts[0]["id"]},
                      filters={"id": f"eq.{bot_id}"})
        print(f"linked to exchange account {accounts[0]['label']}", flush=True)
        return accounts[0]
    except Exception as exc:
        print(f"could not link an exchange account: {exc}", flush=True)
    return None


def _selfcheck_loop(client, account, bot_id, owner_id):
    """Verify our own credentials here, where the keys are, and publish it.

    The public app cannot do this: it holds no keys, on purpose. Rather than
    hand it a key or a way to drive this bot, the answer is measured here and
    read back out of the database.
    """
    from app.validation import selfcheck

    while True:
        try:
            outcome = selfcheck.run(
                client,
                account=account,
                bot_instance_id=bot_id,
                owner_id=owner_id,
                stake_currency=config["stake_currency"],
                stake_amount=config["stake_amount"],
                max_open_trades=config["max_open_trades"],
            )
            if outcome:
                print(f"self-check: {outcome.status} -- {outcome.summary}", flush=True)
        except Exception as exc:
            # Never let this stop the bot trading; a missing result reads as
            # "not measured", which is true.
            print(f"self-check failed: {exc}", flush=True)
        time.sleep(SELFCHECK_INTERVAL_SECONDS)


def register_and_heartbeat():
    """Register this bot and keep its heartbeat fresh.

    Best effort throughout: the bot must trade even when the control plane is
    unreachable. Every failure here is logged and swallowed.
    """
    try:
        from app.core.supabase import SupabaseClient
    except Exception as exc:
        print(f"bot registration unavailable: {exc}", flush=True)
        return

    try:
        client = SupabaseClient.service()
    except Exception as exc:
        print(f"bot registration skipped: {exc}", flush=True)
        return

    owner_id = _env("PLATFORM_OWNER_ID") or _sole_profile_id(client)
    now = datetime.now(timezone.utc).isoformat()
    row = {
        "name": bot_name,
        "owner_id": owner_id,
        "exchange": exchange_name,
        "strategy": strategy,
        "trading_mode": "dry_run" if dry_run else "live",
        "stake_currency": config["stake_currency"],
        "stake_amount": config["stake_amount"],
        "max_open_trades": config["max_open_trades"],
        "deploy_target": _env("DEPLOY_TARGET", "render"),
        "environment": _env("ENVIRONMENT", "production"),
        "db_schema": db_schema,
        # Whatever FREQTRADE_API_BASE_URL says, and nothing invented if it is
        # unset. The obvious guess -- http://<service-name>:<PORT> -- is wrong on
        # Render: it appends a suffix to the name and fronts the service on its
        # own internal port, so the real address looks like
        # http://freqtrade-bot-hn7v:10000. Recording a plausible-looking address
        # that does not resolve is worse than recording none, because the
        # dashboard then reports the bot as down rather than as unconfigured.
        "api_base_url": _env("FREQTRADE_API_BASE_URL"),
        "status": "running",
        "started_at": now,
        "last_heartbeat_at": now,
    }

    bot_id = None
    try:
        existing = client.select_one(
            "bot_instances", columns="id", filters={"name": f"eq.{bot_name}"}
        )
        if existing:
            bot_id = existing["id"]
            client.update("bot_instances", row, filters={"id": f"eq.{bot_id}"})
        else:
            bot_id = client.insert("bot_instances", row)[0]["id"]
        print(f"registered bot instance {bot_id}", flush=True)
    except Exception as exc:
        print(f"could not register this bot: {exc}", flush=True)

    account = _link_account(client, bot_id, owner_id) if bot_id and owner_id else None
    if account:
        threading.Thread(
            target=_selfcheck_loop, args=(client, account, bot_id, owner_id),
            daemon=True, name="selfcheck",
        ).start()

    # Build the live views once freqtrade has created its tables. On a first
    # boot they do not exist yet, so retry for a while rather than giving up.
    def build_views():
        for attempt in range(30):
            time.sleep(20)
            try:
                result = client.rpc("refresh_freqtrade_views", {"p_schema": db_schema})
                if result and "created" in str(result):
                    print(f"live views ready: {result}", flush=True)
                    return
            except Exception as exc:
                if attempt == 0:
                    print(f"live views not ready yet: {exc}", flush=True)

    threading.Thread(target=build_views, daemon=True, name="views").start()

    def beat():
        while True:
            time.sleep(60)
            if not bot_id:
                return
            try:
                client.update(
                    "bot_instances",
                    {"last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
                     "status": "running"},
                    filters={"id": f"eq.{bot_id}"},
                )
            except Exception:
                pass  # a missed heartbeat shows up as 'stale', which is accurate

    threading.Thread(target=beat, daemon=True, name="heartbeat").start()


register_and_heartbeat()

os.makedirs("user_data/strategies", exist_ok=True)
os.system("cp strategies/*.py user_data/strategies/ 2>/dev/null || true")

argv = [
    "trade",
    "--config", "config/config.json",
    "--strategy", strategy,
    "--strategy-path", "strategies",
    "--userdir", "user_data",
]
if db_url:
    argv += ["--db-url", db_url]

print(f"starting freqtrade on port {port}", flush=True)
sys.stdout.flush()
sys.stderr.flush()

# freqtrade runs in this process rather than via execvp, because the numpy
# adapters below have to be registered in the interpreter that does the
# inserting. exec would replace this process and discard them.
if db_url:
    try:
        from app.core.numpy_pg import register as register_numpy_adapters

        if register_numpy_adapters():
            print("psycopg2: numpy adapters registered", flush=True)
        else:
            print(
                "WARNING: could not register numpy adapters. Writes carrying numpy "
                "values will fail with 'schema \"np\" does not exist'.",
                flush=True,
            )
    except Exception as exc:
        print(f"WARNING: numpy adapter registration failed: {exc}", flush=True)

from freqtrade.main import main as freqtrade_main

sys.exit(freqtrade_main(argv))
