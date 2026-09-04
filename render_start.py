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


def _desired_state():
    """running / paused / stopped, as the dashboard last left this bot.

    Best effort in both directions: a bot that cannot reach the control plane
    starts trading, because that is what it was deployed to do, and an
    unrecognised value is treated as no answer rather than trusted into
    freqtrade's config validator.
    """
    override = _env("FREQTRADE_INITIAL_STATE")
    if override in ("running", "paused", "stopped"):
        return override
    try:
        from app.core.supabase import SupabaseClient

        row = SupabaseClient.service().select_one(
            "bot_instances", columns="metadata,trading_mode",
            filters={"name": f"eq.{bot_name}"},
        ) or {}
        metadata = row.get("metadata") or {}

        # Switching between dry-run and live against a shared database is the one
        # boot worth refusing. freqtrade's trades table does not say which mode
        # wrote a row, so a live bot inheriting a dry run's open position will
        # try to sell coins it never bought -- and keep retrying. Come up
        # stopped and let a person look first.
        mode = "dry_run" if dry_run else "live"
        was = row.get("trading_mode")
        if was and was != mode:
            print(f"MODE CHANGE {was} -> {mode}: starting stopped. Clear ft_main of the "
                  f"other mode's trades, then start the bot from the dashboard.", flush=True)
            return "stopped"

        state = metadata.get("desired_state")
        if state in ("running", "paused", "stopped"):
            if state != "running":
                print(f"starting {state}: the dashboard last asked for this", flush=True)
            return state
    except Exception as exc:  # noqa: BLE001
        print(f"could not read desired state ({exc}); starting running", flush=True)
    return "running"


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
    # Not hardcoded to "running": freqtrade keeps its running/stopped state in
    # memory, so a redeploy would restart a bot somebody deliberately stopped --
    # with real money and a live strategy, that is the wrong default. The
    # dashboard records what was asked for; this reads it back.
    "initial_state": _desired_state(),
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

# A connection is what holds a Postgres advisory lock, so this has to outlive the
# function that took it. Module level, deliberately.
_trading_lock_conn = None


def _lock_keys(name):
    """Two keys per bot: the trading lock, and a flag meaning someone wants it."""
    import hashlib

    digest = hashlib.sha256(name.encode()).digest()
    held = int.from_bytes(digest[:8], "big", signed=True)
    wanted = int.from_bytes(digest[8:16], "big", signed=True)
    return held, wanted


def acquire_trading_lock(url, name, wait_seconds=300):
    """Become the only instance that trades, taking over from a predecessor.

    Freqtrade assumes it is alone. It keeps open positions in memory and checks
    "do I already hold this pair?" against that, not against the database, so two
    processes sharing one database each believe a pair is free and both enter. A
    rolling deploy did exactly that here: two trades on the same pair in the same
    second.

    A Postgres advisory lock is the right shape -- it lives on a connection, so it
    releases by itself however a process dies. But refusing to start without it
    deadlocked the deploy: Render keeps the old instance running until the new one
    is healthy, and the new one could never become healthy while the old one held
    the lock. The service became undeployable.

    So there are two keys. HELD is the trading lock. WANTED is raised by a
    newcomer before it waits, and the holder watches it: when WANTED stops being
    free, the holder knows someone is waiting and exits, releasing HELD. Nothing
    is stored and nothing needs cleaning up -- both keys live on connections, so a
    crash at any point leaves no stale state.
    """
    global _trading_lock_conn
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 missing; cannot take the trading lock", flush=True)
        return False

    held_key, wanted_key = _lock_keys(name)
    raw = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    try:
        conn = psycopg2.connect(raw, connect_timeout=20)
        conn.autocommit = True
    except Exception as exc:
        print(f"could not connect to take the trading lock: {exc}", flush=True)
        return False

    def try_lock(key):
        with conn.cursor() as cur:
            cur.execute("select pg_try_advisory_lock(%s)", (key,))
            return bool(cur.fetchone()[0])

    def unlock(key):
        with conn.cursor() as cur:
            cur.execute("select pg_advisory_unlock(%s)", (key,))

    # Announce the intent first, so an incumbent sees it and stands down.
    #
    # Retried, because the incumbent's own watcher takes and releases WANTED
    # every few seconds to test whether anyone is waiting. A single attempt that
    # lands inside that window sees the incumbent's own probe, concludes a third
    # instance is queued, and exits -- which fails the deploy and leaves the old
    # instance running. The contention we actually care about lasts as long as
    # another newcomer is waiting, not one round trip.
    deadline = time.time() + wait_seconds
    announced = False
    while time.time() < deadline:
        if try_lock(wanted_key):
            announced = True
            break
        time.sleep(1)
    if not announced:
        print("another instance is already waiting to take over; deferring to it",
              flush=True)
        conn.close()
        return False

    said = False
    while True:
        if try_lock(held_key):
            unlock(wanted_key)
            _trading_lock_conn = conn
            print(f"holding the trading lock for {name}", flush=True)
            return True
        if not said:
            print(f"another instance holds the trading lock for {name}; it has been "
                  "asked to stand down. Waiting for it to exit.", flush=True)
            said = True
        if time.time() >= deadline:
            conn.close()
            print(
                "TRADING LOCK NOT ACQUIRED: the previous instance did not stand "
                "down. Refusing to start a second trading process -- two of them "
                "on one database open duplicate positions.",
                flush=True,
            )
            return False
        time.sleep(3)


def watch_for_takeover(name, on_yield, poll_seconds=5):
    """Stand down when a newer instance asks for the trading lock.

    The incumbent has to release, or a rolling deploy can never complete: the
    replacement is not healthy until it holds the lock, and the platform will not
    stop the incumbent until the replacement is healthy.
    """
    _, wanted_key = _lock_keys(name)

    def watch():
        failures = 0
        while True:
            time.sleep(poll_seconds)
            conn = _trading_lock_conn
            if conn is None:
                return
            try:
                with conn.cursor() as cur:
                    # Free means nobody is waiting. Take and release it rather
                    # than holding it, so the next newcomer can raise it again.
                    cur.execute("select pg_try_advisory_lock(%s)", (wanted_key,))
                    free = bool(cur.fetchone()[0])
                    if free:
                        cur.execute("select pg_advisory_unlock(%s)", (wanted_key,))
                failures = 0
            except Exception as exc:
                # A dropped query here is not just a missed poll. Advisory locks
                # live on the connection that took them, so if this connection
                # is gone the lock is already released server-side -- and this
                # process is still trading, believing it holds it. Another
                # instance can now take it and open a position on the same pair,
                # which is the duplicate-trade failure the lock exists to
                # prevent. Observed 2026-08-31, when Supabase's pooler dropped
                # every connection at once.
                failures += 1
                print(f"takeover watch failed ({failures}): {exc}", flush=True)
                if failures >= 3:
                    print("TRADING LOCK CONNECTION LOST: the lock is released "
                          "server-side while this process is still trading. "
                          "Exiting so a replacement can take it cleanly.",
                          flush=True)
                    on_yield()
                    return
                continue
            if not free:
                print("a newer instance wants the trading lock; standing down",
                      flush=True)
                on_yield()
                return

    threading.Thread(target=watch, daemon=True, name="takeover").start()


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

#: Reconcile every fourth self-check -- hourly at the default interval. It costs
#: a request per traded pair, and orders do not change faster than that.
RECONCILE_EVERY_N_CHECKS = 4


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


#: How often to look for an on-demand verification request. Small enough that
#: pressing the button feels like it did something, large enough that it is
#: three queries a minute.
VERIFY_POLL_SECONDS = int(_env("VERIFY_POLL_SECONDS", "20") or 20)


def _verify_requested(client, bot_id, state) -> bool:
    """Has the dashboard asked for a check since the last one we ran?"""
    if not bot_id:
        return False
    try:
        row = client.select_one("bot_instances", columns="metadata",
                                filters={"id": f"eq.{bot_id}"}) or {}
        asked = (row.get("metadata") or {}).get("verify_requested_at")
    except Exception:  # noqa: BLE001 - a missed poll is not worth a log line
        return False
    if not asked or asked == state.get("last_request"):
        return False
    state["last_request"] = asked
    return True


def _strategy_sha() -> str | None:
    """sha256 of the strategy file this bot is about to run.

    The name alone cannot tell an edit from a rename: a strategy iterated on in
    place keeps its class name, so two months of trades can carry one label and
    be materially different code.
    """
    import hashlib
    from pathlib import Path

    for base in ("strategies", "user_data/strategies"):
        path = Path(base) / f"{strategy}.py"
        if path.exists():
            return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def _record_deployment(client, bot_id, owner_id) -> None:
    """Open a deployment row, closing the previous one if this is a change.

    One open row per bot, enforced by a partial unique index. Rotating rather
    than overwriting is what makes the history usable: anything timestamped --
    trades, signals, drawdown -- can be attributed by asking which deployment
    covered that moment.
    """
    if not bot_id:
        return
    try:
        sha = _strategy_sha()
        open_rows = client.select(
            "strategy_deployments", columns="id,strategy,source_sha",
            filters={"bot_instance_id": f"eq.{bot_id}", "ended_at": "is.null"},
            limit=1,
        )
        current = open_rows[0] if open_rows else None
        if current and current.get("strategy") == strategy and current.get("source_sha") == sha:
            return                              # same code, still running

        if current:
            client.update("strategy_deployments",
                          {"ended_at": datetime.now(timezone.utc).isoformat()},
                          filters={"id": f"eq.{current['id']}"})
            what = "edited" if current.get("strategy") == strategy else "replaced"
            print(f"strategy {what}: {current.get('strategy')} -> {strategy}", flush=True)

        client.insert("strategy_deployments", {
            "owner_id": owner_id,
            "bot_instance_id": bot_id,
            "strategy": strategy,
            "source_sha": sha,
            "stake_amount": config["stake_amount"],
            "max_open_trades": config["max_open_trades"],
            "trading_mode": "dry_run" if dry_run else "live",
        })
        print(f"deployment recorded: {strategy} ({(sha or '')[:12]})", flush=True)
    except Exception as exc:  # noqa: BLE001 - attribution must never stop trading
        print(f"could not record the deployment: {exc}", flush=True)


def _local_bot_client():
    """A client for this bot's own REST API, over the loopback interface."""
    from app.bot_api import BotClient

    return BotClient(
        f"http://127.0.0.1:{port}",
        config["api_server"]["username"],
        config["api_server"]["password"],
    )


def _record_signals(client, bot_id, owner_id) -> int:
    """Store this strategy's entry and exit signals for the watched pairs."""
    from app.validation import signals

    bot = _local_bot_client()

    # This loop starts before freqtrade does -- registration runs first, and the
    # API server comes up seconds later. Without a wait the first pass after
    # every deploy reads nothing and the next one is fifteen minutes away, which
    # on a day of frequent deploys is most of the day.
    for _ in range(60):
        try:
            bot.get("ping")
            break
        except Exception:  # noqa: BLE001 - not up yet
            time.sleep(2)
    else:
        return 0

    whitelist = (bot.get("whitelist") or {}).get("whitelist") or []
    if not whitelist:
        return 0
    # The strategy owns the timeframe, not this config -- TrendPullbackStrategy
    # runs on 4h and nothing here says so. Ask the bot what it resolved to
    # rather than keeping a second copy that can disagree.
    timeframe = (bot.get("show_config") or {}).get("timeframe")
    if not timeframe:
        return 0
    return signals.record(
        client, bot=bot,
        # The pairs it is actually watching, capped: this is one request each
        # and the point is the pairs it might trade, not every pair on earth.
        pairs=whitelist[:25],
        timeframe=timeframe,
        owner_id=owner_id,
        bot_instance_id=bot_id,
        exchange=exchange_name,
    )


def _stamp_verify_ran(client, bot_id) -> None:
    """Record that a check completed, so the page can tell fresh from stale."""
    if not bot_id:
        return
    try:
        row = client.select_one("bot_instances", columns="metadata",
                                filters={"id": f"eq.{bot_id}"}) or {}
        metadata = dict(row.get("metadata") or {})
        metadata["verify_ran_at"] = datetime.now(timezone.utc).isoformat()
        client.update("bot_instances", {"metadata": metadata},
                      filters={"id": f"eq.{bot_id}"})
    except Exception:  # noqa: BLE001 - the run itself is already recorded
        pass


def _selfcheck_loop(client, account, bot_id, owner_id):
    """Verify our own credentials here, where the keys are, and publish it.

    The public app cannot do this: it holds no keys, on purpose. Rather than
    hand it a key or a way to drive this bot, the answer is measured here and
    read back out of the database.
    """
    from app.validation import selfcheck

    # Seeded from whatever is already on the row, so a request made while the
    # bot was down does not fire the moment it comes back and then again on its
    # own schedule.
    nonlocal_state = {"ticks": 0, "last_request": None}
    _verify_requested(client, bot_id, nonlocal_state)
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
                _stamp_verify_ran(client, bot_id)

            # Reconciliation asks the venue what it actually did with the orders
            # this bot recorded. Hourly rather than every cycle: it is a request
            # per traded pair, and the answer moves at the speed of trading.
            # Copy closed trades somewhere freqtrade cannot reset. ft_main is
            # its working store, not a record: the cutover cleared it, and the
            # only reason nothing was lost is that nothing had been archived
            # since the Railway import anyway.
            try:
                from app.validation import archive

                moved = archive.sync(client, bot_instance_id=bot_id, owner_id=owner_id,
                                     trading_mode="dry_run" if dry_run else "live")
                if moved:
                    print(f"archive: {moved} closed trade(s) stored", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"could not archive trades: {exc}", flush=True)

            # What the strategy said, recorded before anything could get in the
            # way. Read from this bot's own API over the loopback: the analysed
            # dataframe lives in the freqtrade process, not in this thread, and
            # its own REST interface is the supported way to reach it.
            try:
                stored = _record_signals(client, bot_id, owner_id)
                if stored:
                    print(f"signals: recorded {stored}", flush=True)
            except Exception as exc:  # noqa: BLE001
                print(f"could not record signals: {exc}", flush=True)

            nonlocal_state["ticks"] += 1
            if nonlocal_state["ticks"] % RECONCILE_EVERY_N_CHECKS == 1:
                matched = selfcheck.reconcile(
                    client, account=account, bot_instance_id=bot_id, owner_id=owner_id,
                )
                if matched:
                    print(f"reconciliation: {matched.status} -- {matched.summary}",
                          flush=True)
        except Exception as exc:
            # Never let this stop the bot trading; a missing result reads as
            # "not measured", which is true.
            print(f"self-check failed: {exc}", flush=True)

        # Sleep in slices so a "verify now" from the dashboard does not wait out
        # the full interval. The app cannot call this directly -- it holds no
        # exchange keys and has no route into this process -- so the request
        # arrives as a timestamp on the bot's own row.
        waited = 0
        while waited < SELFCHECK_INTERVAL_SECONDS:
            time.sleep(VERIFY_POLL_SECONDS)
            waited += VERIFY_POLL_SECONDS
            if _verify_requested(client, bot_id, nonlocal_state):
                print("verification requested from the dashboard; running now", flush=True)
                # Force the reconciliation branch too: an on-demand check that
                # skipped the trade-by-trade comparison would answer a different
                # question from the one that was asked.
                nonlocal_state["ticks"] = 0
                break


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

    _record_deployment(client, bot_id, owner_id)

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

# Nothing above this line places an order. Everything below does, so this is
# where being the only trading instance stops being optional.
#
# The lock is taken *after* freqtrade is serving, not before, and the process
# starts in STOPPED state regardless of what was asked for. That ordering is the
# whole point:
#
#   Render will not stop the incumbent until the replacement is healthy, and a
#   private service is healthy when its port answers. Taking the lock first made
#   the replacement wait on a lock the incumbent would not release until the
#   replacement was healthy -- a deadlock. Making the incumbent stand down broke
#   the deadlock but exchanged it for a different failure: a deliberate exit
#   looks exactly like a crash to the platform, so every deploy that used it was
#   marked failed even when the handover worked perfectly.
#
#   Serving first means the replacement is healthy in seconds without trading,
#   Render stops the incumbent in its own time, the incumbent's connection dies,
#   the lock frees, and the replacement starts trading. Nobody exits early and
#   only one process ever trades.
def _take_lock_then_trade():
    """Wait for the port, take the lock, then start trading -- in that order."""
    import base64
    import urllib.error
    import urllib.request

    auth = base64.b64encode(
        f"{config['api_server']['username']}:{config['api_server']['password']}".encode()
    ).decode()

    def local(path, method="GET"):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/v1/{path}", method=method)
        req.add_header("Authorization", f"Basic {auth}")
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode() or "{}")

    for _ in range(120):
        try:
            local("ping")
            break
        except Exception:  # noqa: BLE001 - it is simply not up yet
            time.sleep(1)
    else:
        print("freqtrade never answered locally; not taking the trading lock", flush=True)
        return

    if not acquire_trading_lock(db_url, bot_name, wait_seconds=1800):
        print("TRADING LOCK NOT ACQUIRED: staying stopped rather than running a second "
              "trading process against one database.", flush=True)
        return

    # Only now is trading safe. Honour whatever state was actually asked for.
    wanted = _desired_state()
    if wanted == "running":
        try:
            print(f"lock held; starting the trader ({local('start', 'POST').get('status')})",
                  flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"could not start the trader: {exc}", flush=True)
    else:
        print(f"lock held; staying {wanted} as asked", flush=True)

    # Still watched, as a fallback: if the platform ever leaves two instances
    # up, the older one gives way rather than both sitting on one database.
    def stand_down():
        print("a newer instance is waiting and the platform has not stopped this one; "
              "yielding the lock", flush=True)
        os._exit(0)

    watch_for_takeover(bot_name, stand_down)


if db_url:
    # Serve first, trade second.
    config["initial_state"] = "stopped"
    with open("config/config.json", "w") as handle:
        json.dump(config, handle, indent=2)
    threading.Thread(target=_take_lock_then_trade, daemon=True, name="trading-lock").start()

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

def _make_the_db_connection_survivable() -> bool:
    """Give freqtrade's engine a health check, since it does not build one.

    freqtrade creates its engine as `create_engine(db_url, future=True)` and
    only ever adds kwargs for sqlite, so a Postgres connection gets no
    liveness check at all. Supabase's pooler drops connections -- on its own
    maintenance, and on idle -- and the first query afterwards fails with
    "SSL SYSCALL error: EOF detected". freqtrade logs "Fatal exception!" and
    exits 1. That killed the live bot twice in ten days, mid-session, while it
    was holding open positions.

    pool_pre_ping makes SQLAlchemy check a pooled connection with a cheap
    round trip before handing it out, and transparently replace a dead one. It
    is the standard answer to exactly this, and there is no config option for
    it, so it goes in here. Patched rather than vendored: freqtrade is a
    dependency and this survives upgrading it.
    """
    try:
        from freqtrade.persistence import models

        original = models.create_engine

        def create_engine(url, **kwargs):
            kwargs.setdefault("pool_pre_ping", True)
            # Recycle well inside the pooler's own idle timeout, so connections
            # are replaced on our schedule rather than dropped on its.
            kwargs.setdefault("pool_recycle", 900)
            return original(url, **kwargs)

        models.create_engine = create_engine
        return True
    except Exception as exc:  # noqa: BLE001 - better to trade without it than not at all
        print(f"WARNING: could not enable pool_pre_ping ({exc}); a dropped database "
              "connection will be fatal", flush=True)
        return False


if db_url:
    if _make_the_db_connection_survivable():
        print("database: pool_pre_ping on, connections recycled every 15m", flush=True)

from freqtrade.main import main as freqtrade_main

sys.exit(freqtrade_main(argv))
