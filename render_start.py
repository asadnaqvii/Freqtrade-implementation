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


def _env_list(name, default):
    """A comma-separated env var as a list, or the default when unset.

    Set but empty is a deliberate empty list, not "use the default": clearing a
    blacklist has to be expressible, and silently reinstating one somebody meant
    to remove is the kind of surprise that only shows up in a trade.
    """
    raw = _env(name)
    if raw is None:
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


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
environment = _env("ENVIRONMENT", "production") or "production"


def _refuse_live_outside_production(environment, dry_run):
    """Why this process must not start, or None.

    A staging bot exists so that nothing in it can reach the live account. Two
    settings decide that, ENVIRONMENT and DRY_RUN, and one wrong edit to either
    would quietly turn a rehearsal into real orders. So the check is here,
    before a config is written or an exchange is contacted, and it fails the
    deploy rather than starting carefully.
    """
    if environment != "production" and not dry_run:
        return (f"REFUSING TO START: ENVIRONMENT={environment!r} but DRY_RUN is not "
                "true. A bot outside production never trades live. Set DRY_RUN=true, "
                "or ENVIRONMENT=production if this really is the live bot.")
    return None


def _credentials_a_dry_run_does_not_need(dry_run, names_present):
    """A warning naming the exchange credentials a dry run is carrying, or None.

    Dry-run uses none of them, and their absence is what makes an environment
    unable to trade for real. Not fatal -- a live bot flipped to dry-run for a
    moment should not be locked out -- but loud.
    """
    if dry_run and names_present:
        return ("WARNING: DRY_RUN=true but exchange credentials are set ("
                + ", ".join(names_present) + "). A dry run does not use them; remove "
                "them so this environment can never trade for real.")
    return None


_refusal = _refuse_live_outside_production(environment, dry_run)
if _refusal:
    print(_refusal, flush=True)
    sys.exit(1)
_carrying = _credentials_a_dry_run_does_not_need(dry_run, [v for v in REQUIRED if _env(v)])
if _carrying:
    print(_carrying, flush=True)


#: Seconds to give the control plane for the boot-time read of what was asked
#: for. It runs before the config is written and before the port answers, so
#: a slow Supabase here is minutes of nothing serving. Best effort anyway.
DESIRED_STATE_TIMEOUT = 5


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

        row = SupabaseClient.service(timeout=DESIRED_STATE_TIMEOUT).select_one(
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
    "tradable_balance_ratio": float(_env("FREQTRADE_TRADABLE_RATIO", "0.95") or 0.95),
    "fiat_display_currency": "USD",
    "dry_run": dry_run,
    "dry_run_wallet": 1000,
    "cancel_open_orders_on_exit": False,
    "unfilledtimeout": {"entry": 30, "exit": 30, "exit_timeout_count": 0, "unit": "minutes"},
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
        # Leveraged tokens and wrappers are structural: they are not what the
        # strategy models. The named coins come from v3's config, picked off
        # this account's own performance-by-pair numbers rather than a hunch --
        # revisit them as more data arrives, since a pair losing over two or
        # three trades is not proven bad, only bad so far.
        "pair_blacklist": _env_list(
            "FREQTRADE_PAIR_BLACKLIST",
            ["BNB/.*", ".*UP/USDT", ".*DOWN/USDT", ".*BEAR/USDT", ".*BULL/USDT",
             "ZEC/USDT", "UNI/USDT", "SHIB/USDT", "PEPE/USDT", "AAVE/USDT"],
        ),
    },
    "pairlists": [
        # A floor on quote volume. This was 0 -- no floor at all -- so the top
        # 25 by volume could still include a pair thin enough that the spread
        # eats the edge the strategy is trying to capture.
        {"method": "VolumePairList", "number_assets": 25, "sort_key": "quoteVolume",
         "min_value": float(_env("FREQTRADE_MIN_QUOTE_VOLUME", "5000000") or 0),
         "refresh_period": 3600},
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
db_url_fallback = None
learning_enabled = False
learning_outbox_path = "user_data/learning_outbox.sqlite"
learning_write_interval = 2.0
try:
    from app.core.config import get_settings

    settings = get_settings()
    db_url = settings.freqtrade_db_url
    db_url_fallback = settings.freqtrade_db_url_fallback
    learning_enabled = settings.learning.enabled
    learning_outbox_path = settings.learning.outbox_path
    learning_write_interval = settings.learning.write_interval_seconds
except Exception as exc:
    print(f"WARNING: could not read platform settings ({exc}); using SQLite", flush=True)

#: What the Learning Module stamps on every record. The name is known now;
#: registration fills in the instance and account ids when it has them.
learning_identity = {"bot_name": bot_name, "bot_instance_id": None,
                     "owner_id": _env("PLATFORM_OWNER_ID"), "account_id": None}

# A connection is what holds a Postgres advisory lock, so this has to outlive the
# function that took it. Module level, deliberately.
_trading_lock_conn = None

# Set once this instance has yielded the trading lock. The heartbeat checks it:
# a process that has stood down must stop claiming to be the live bot, or the
# dashboard shows the outgoing instance as healthy while the incoming one works.
_stood_down = threading.Event()

#: How long to wait for the platform to stop a stood-down instance before
#: exiting anyway. Only reached if the platform never terminates us, which a
#: rolling deploy always does; leaving an inert process up forever is worse than
#: the one failure notice exiting costs. Nothing trades during this window, so
#: it is short.
STANDDOWN_EXIT_AFTER = 300

#: How long after taking the lock to disregard the "somebody wants it" flag.
#: Postgres releases a dead connection's advisory locks when it reaps the
#: backend, which is not instant. Inside this window the flag is far more
#: likely to belong to the predecessor this process just replaced than to a
#: replacement for this process, which cannot exist yet.
STARTUP_TAKEOVER_GRACE = 120

#: How long to wait before trying the trading lock again. The database being
#: briefly unreachable must cost a minute, not the life of the process.
LOCK_RETRY_SECONDS = 60

#: How often to confirm the trader is actually trading. Cheap -- one loopback
#: call -- against the failure it catches, which is the bot sitting up and
#: healthy while managing no stop-loss on real positions.
SUPERVISE_SECONDS = 120

#: How long after starting the trader to wait before checking that it stuck,
#: and how many times to try. freqtrade assigns its initial state after the
#: pairlist refresh, so a start that lands before that line is overwritten by
#: it -- observed 2026-09-16 05:53, self-healed two minutes later. Now checked.
START_SETTLE_SECONDS = 5
START_ATTEMPTS = 3

#: Seconds the trading loop may go without going round before the heartbeat
#: reports "hung". Generous: one pass over 25 pairs, plus freqtrade's own 30 s
#: pause on a temporary error, plus the 30 s pause added below, is well inside.
LOOP_STALL_SECONDS = 300

#: The dead-man's switch: a URL that expects to be pinged, and tells a person
#: when the pings stop. Pinged only while the trader is verifiably trading, so
#: silence -- a crash, a hang, a stop, Supabase down, Render down -- is the
#: alarm, and it does not depend on any part of this system to be raised.
HEARTBEAT_URL = (os.environ.get("HEARTBEAT_URL") or "").strip()

#: Set by the patched trading loop each time it goes round. None until then.
_last_loop_at = None

#: The FreqtradeBot instance once it exists, and the event that says so. Set
#: by the patched constructor, which returns only after freqtrade has assigned
#: its initial state -- so anything waiting on this cannot race that line.
_bot_holder = {}
_bot_ready = threading.Event()


def _first_line(exc):
    """The first line of an exception's message, or its repr when empty."""
    text = str(exc).strip()
    return text.splitlines()[0][:200] if text else repr(exc)


def _trader_state():
    """The trader's state as freqtrade holds it in memory, lower-case, or None
    when there is no handle on the bot yet."""
    bot = _bot_holder.get("bot")
    if bot is None:
        return None
    return str(bot.state).lower()


def _set_trader_state(name):
    """Flip the trader in-process -- exactly what /start and /stop do from the
    API thread, without depending on that thread being able to answer.

    Returns the state afterwards, or None when there is no handle yet."""
    bot = _bot_holder.get("bot")
    if bot is None:
        return None
    from freqtrade.enums import State

    bot.state = State[name.upper()]
    return str(bot.state).lower()


def _loop_is_stalled():
    """Has the trading loop stopped going round? False until it has started."""
    return (_last_loop_at is not None
            and time.monotonic() - _last_loop_at > LOOP_STALL_SECONDS)


def _ping_heartbeat(path=""):
    """Tell the dead-man's switch we are alive -- or, with "/fail", that we are
    not. Never raises: a page that cannot be sent must not stop the trader."""
    if not HEARTBEAT_URL:
        return False
    import urllib.request

    try:
        request = urllib.request.Request(HEARTBEAT_URL.rstrip("/") + path,
                                         data=b"", method="POST")
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status < 300
    except Exception as exc:  # noqa: BLE001 - the switch is a witness, never a dependency
        print(f"heartbeat ping{path} failed: {exc}", flush=True)
        return False


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


def watch_for_takeover(name, on_yield, poll_seconds=5,
                       grace_seconds=None, confirmations=2):
    """Stand down when a newer instance asks for the trading lock.

    The incumbent has to release, or a rolling deploy can never complete: the
    replacement is not healthy until it holds the lock, and the platform will not
    stop the incumbent until the replacement is healthy.

    `on_yield` is called with why: "takeover" when a replacement asked for the
    lock, which is routine, or "lock_lost" when this process's own lock
    connection died, which is a fault. They need different endings.
    """
    _, wanted_key = _lock_keys(name)
    grace = STARTUP_TAKEOVER_GRACE if grace_seconds is None else grace_seconds

    def watch():
        failures = 0
        wanted = 0
        started_at = time.monotonic()
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
                    on_yield("lock_lost")
                    return
                continue
            if not free:
                # A held wanted-flag is not proof a replacement is asking. When
                # this process was OOM-killed on 2026-09-05 the flag its own
                # predecessor held had not been reaped yet, so the replacement
                # read its dead ancestor as a newcomer, stood down 67 seconds
                # after starting, stopped trading and killed its heartbeat --
                # leaving a live process holding four positions, managing none
                # of them, and invisible to every dashboard. The worst state the
                # system can be in, reached by the code meant to prevent it.
                #
                # A real replacement holds the flag until it gets the lock, so
                # it is still there on the next poll. A dead connection's is
                # gone within seconds. Requiring two consecutive sightings, and
                # ignoring the flag entirely while the previous holder is still
                # being reaped, tells them apart.
                if time.monotonic() - started_at < grace:
                    continue
                wanted += 1
                if wanted < confirmations:
                    print("something holds the wanted-lock flag; confirming "
                          "before standing down", flush=True)
                    continue
                print("a newer instance wants the trading lock; standing down",
                      flush=True)
                on_yield("takeover")
                return
            else:
                wanted = 0

    threading.Thread(target=watch, daemon=True, name="takeover").start()


def stand_down(reason, local):
    """Go inert and let the platform stop us, rather than exiting.

    This used to call os._exit(0). It worked -- the lock was released and
    the replacement started -- but Render classes any self-initiated exit as
    `earlyExit`, records `server_failed`, and emails "your service failed".
    Every deploy therefore sent a crash notice for a handover that went
    exactly to plan, which is indistinguishable from the two real crashes on
    31 August and 4 September. A crash alert that fires when nothing is
    wrong is one you learn to skim.

    Standing down never required exiting. The deadlock it breaks is that the
    replacement is not healthy until it holds the lock, and the platform will
    not stop the incumbent until the replacement is healthy -- so releasing
    the lock is sufficient. Render then terminates this process by SIGTERM in
    the ordinary way, and records no failure.
    """
    global _trading_lock_conn

    _stood_down.set()

    # Stop trading first. Between here and being terminated this process
    # must not act on the market, and the replacement is about to. Done
    # in-process where possible: the API thread can be wedged on a dead
    # database connection, and a /stop that timed out used to be swallowed --
    # releasing the lock below while this process's loop was still RUNNING,
    # which is two live traders on one account, the exact thing the lock
    # exists to prevent. The HTTP route stays for a process with no handle.
    stopped = _set_trader_state("stopped")
    if stopped is not None:
        print(f"stopped trading in-process (state {stopped})", flush=True)
    else:
        try:
            print(f"stopped trading ({local('stop', 'POST').get('status')})", flush=True)
        except Exception as exc:  # noqa: BLE001 - releasing the lock still matters
            print(f"could not stop the trader before standing down: {exc}", flush=True)

    # Closing the connection releases the advisory lock server-side, which
    # is what the replacement is waiting on.
    conn, _trading_lock_conn = _trading_lock_conn, None
    if conn is not None:
        try:
            conn.close()
        except Exception as exc:  # noqa: BLE001
            print(f"could not close the lock connection: {exc}", flush=True)

    if reason == "lock_lost":
        # Nothing is waiting to replace us: our own lock connection died, so
        # the lock is already gone server-side and no other instance has
        # been started. Going inert here would leave nothing trading at all
        # while the service still looked up. Exit non-zero so the platform
        # starts a fresh process -- and so the failure notice this sends is
        # a true one, unlike the handover above.
        print("the lock connection is gone and no replacement is waiting; "
              "exiting so the platform starts a fresh instance", flush=True)
        _ping_heartbeat("/fail")
        os._exit(1)
        return  # unreachable in production; os._exit does not return

    print("trading lock released; waiting for the platform to stop this "
          "instance", flush=True)

    # The replacement is trading; this process has a few seconds to ship what
    # the Learning Module queued. Bounded, and nothing depends on it.
    try:
        import app.learning as _learning

        _learning.flush(5.0)
    except Exception:  # noqa: BLE001 - the outbox file keeps what did not go
        pass

    # Nothing left to do but wait to be terminated. This runs on a daemon
    # thread, so a SIGTERM from the platform ends it with the process; the
    # exit below is only reached if that never comes.
    time.sleep(STANDDOWN_EXIT_AFTER)
    print(f"still running {STANDDOWN_EXIT_AFTER}s after standing down; the "
          "platform has not stopped this instance, so exiting rather than "
          "sitting here inert.", flush=True)
    os._exit(0)


# --- database check ---
#: Waits between attempts to reach the database at boot: about nine minutes in
#: all before the process concedes and starts anyway. A transient pooler error
#: here used to be `sys.exit(1)` on the first try -- a crash loop the platform
#: backs off to fifteen-minute intervals, for a blip that lasted seconds.
DB_VERIFY_WAITS = (15, 30, 60, 60, 60, 60, 60, 60, 60)
#: The fallback url (the pooler, once the direct host is the default) gets a
#: shorter run: if neither answers, waiting longer is not the fix.
DB_FALLBACK_WAITS = (15, 30)


def _explain_connection_failure(detail, raw):
    """Turn the two connection errors people actually hit into instructions."""
    pooled = "pooler.supabase.com" in raw
    if "ENOTFOUND" in detail or "Tenant or user not found" in detail:
        if pooled:
            print(
                "  The pooler does not recognise that user. Supabase's pooler expects "
                "<role>.<project-ref> as the username, and the host must be the pooler "
                "shown in Settings -> Database -> Connection string -> Session pooler.",
                flush=True,
            )
        else:
            print(
                "  That is the pooler's error on a direct host: with the direct host "
                "the username is the plain role (ft_bot), not role.<project-ref>.",
                flush=True,
            )
    elif ("timeout" in detail.lower() or "could not translate host name" in detail
          or "Network is unreachable" in detail):
        print(
            "  Nothing answered. The direct host db.<ref>.supabase.co is IPv6-only "
            "unless the project's IPv4 add-on is enabled; without it, use the session "
            "pooler URI (and set it as SUPABASE_DB_URL_FALLBACK so this boot can fall "
            "back to it).",
            flush=True,
        )


def _connect_with_retry(url, waits=DB_VERIFY_WAITS):
    """A connection, or None once every attempt has failed. Never raises."""
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed; cannot verify the database", flush=True)
        return None

    # psycopg2 wants a plain postgresql:// url, not SQLAlchemy's driver form.
    raw = url.replace("postgresql+psycopg2://", "postgresql://", 1)
    attempts = len(waits) + 1
    for attempt in range(1, attempts + 1):
        try:
            return psycopg2.connect(raw, connect_timeout=20)
        except Exception as exc:  # noqa: BLE001 - every failure here is reported, then retried
            detail = _first_line(exc)
            print(f"DATABASE ERROR: {detail}", flush=True)
            _explain_connection_failure(detail, raw)
            if attempt <= len(waits):
                wait = waits[attempt - 1]
                print(f"  retrying in {wait}s (attempt {attempt} of {attempts})", flush=True)
                time.sleep(wait)
    return None


def _check_schema(conn, expected_schema):
    """True when the session lands in the right schema; prints the fix if not.

    Freqtrade writes unqualified SQL -- `INSERT INTO trades` -- so the schema is
    decided entirely by search_path. Getting that wrong does not fail at
    startup; it fails on the first trade, which is a bad time to find out.
    """
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


def verify_database(url, expected_schema, fallback_url=None):
    """Which url to trade with, and whether we may: (url, verdict).

    The verdict is "ok"; "misconfigured" -- wrong schema or user, which is a
    refusal to start because trading there loses history silently; or
    "unreachable" -- nothing answered after every attempt, in which case the
    process starts anyway: freqtrade retries its first connection and the
    trading lock is retried until it is taken, and a bot that boots into a
    retry beats one the platform restarts every fifteen minutes.
    """
    for candidate, waits, label in ((url, DB_VERIFY_WAITS, "database"),
                                    (fallback_url, DB_FALLBACK_WAITS, "fallback database")):
        if not candidate:
            continue
        if label == "fallback database":
            print("trying the fallback database url", flush=True)
        conn = _connect_with_retry(candidate, waits=waits)
        if conn is None:
            continue
        try:
            ok = _check_schema(conn, expected_schema)
        finally:
            conn.close()
        if label == "fallback database" and ok:
            print("using the fallback database url for this run", flush=True)
        return candidate, ("ok" if ok else "misconfigured")
    return url, "unreachable"


if db_url:
    # Never print the URL; it carries the database password.
    print(f"persistence: postgres, schema {db_schema}", flush=True)
    db_url, verdict = verify_database(db_url, db_schema, db_url_fallback)
    if verdict == "misconfigured":
        print(
            "Refusing to start against a database that is not set up correctly. "
            "Trading with the wrong schema loses trade history silently.",
            flush=True,
        )
        sys.exit(1)
    if verdict == "unreachable":
        print(
            "DATABASE UNREACHABLE after every attempt -- starting anyway. freqtrade "
            "retries its first connection and the trading lock is retried until it "
            "is taken; a bot that boots into a retry beats one the platform restarts "
            "every fifteen minutes.",
            flush=True,
        )
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
        "environment": environment,
        "db_schema": db_schema,
        # Whatever FREQTRADE_API_BASE_URL says, and nothing invented if it is
        # unset. The obvious guess -- http://<service-name>:<PORT> -- is wrong on
        # Render: it appends a suffix to the name, so the real address looks
        # like http://freqtrade-bot-hn7v:8080. The port is PORT (8080), not the
        # 10000 the Render API reports in serviceDetails.url -- that one refuses
        # connections. Recording a plausible-looking address that does not
        # resolve is worse than recording none, because the dashboard then
        # reports the bot as down rather than as unconfigured.
        "api_base_url": _env("FREQTRADE_API_BASE_URL"),
        "status": "running",
        "started_at": now,
        "last_heartbeat_at": now,
    }

    def register():
        """Create or refresh this bot's row. Returns its id, or None.

        Retried from the heartbeat rather than attempted once. Registration
        failing at boot used to be permanent: bot_id stayed None, the heartbeat
        thread returned on its first tick, and the process traded for the rest
        of its life with nothing reporting that it existed.

        That is not hypothetical. Supabase restricted this project for exceeding
        its egress quota on 2026-09-05 and answered 402 to everything. The bot
        kept trading -- freqtrade talks to Postgres directly and never noticed --
        but registration failed, so the heartbeat never started, and the
        dashboard showed it offline for 31 hours across three restarts, long
        after the quota problem itself was resolved.
        """
        try:
            existing = client.select_one(
                "bot_instances", columns="id", filters={"name": f"eq.{bot_name}"}
            )
            if existing:
                client.update("bot_instances", row, filters={"id": f"eq.{existing['id']}"})
                return existing["id"]
            return client.insert("bot_instances", row)[0]["id"]
        except Exception as exc:
            print(f"could not register this bot: {exc}", flush=True)
            return None

    bot_id = register()
    if bot_id:
        print(f"registered bot instance {bot_id}", flush=True)
    learning_identity.update(bot_instance_id=bot_id, owner_id=owner_id)

    _record_deployment(client, bot_id, owner_id)

    account = _link_account(client, bot_id, owner_id) if bot_id and owner_id else None
    learning_identity["account_id"] = account.get("id") if account else None
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
        nonlocal bot_id
        while True:
            time.sleep(60)
            # Stood down: the replacement owns this bot's row now. Checked
            # before the retry below, so a process on its way out does not
            # register itself back over its successor.
            if _stood_down.is_set():
                print("stood down; stopping the heartbeat", flush=True)
                return
            if not bot_id:
                bot_id = register()
                if not bot_id:
                    continue        # keep trying; the outage may be temporary
                print(f"registered bot instance {bot_id} (late)", flush=True)
                learning_identity.update(bot_instance_id=bot_id, owner_id=owner_id)
                _record_deployment(client, bot_id, owner_id)
            # "running" used to be hardcoded, which made the column a statement
            # that the process exists rather than that it is trading. A bot that
            # is up, healthy and STOPPED manages no stop-loss on anything it
            # holds, and every dashboard reading a heartbeat called that fine.
            state = "running"
            try:
                config = _local_bot_client().get("show_config") or {}
                reported = str(config.get("state") or "").lower()
                if reported and reported != "running":
                    state = reported
            except Exception:
                # The API not answering is itself worth recording: the process
                # is alive enough to heartbeat and not alive enough to trade.
                state = "unreachable"
            # A port that answers proves the API thread is alive, not that the
            # trader is. A "Fatal exception!" can leave exactly that: the loop
            # dead, the API up, and a heartbeat that reads "running" for days.
            # The loop itself stamps the clock; five silent minutes is "hung".
            if state == "running" and _loop_is_stalled():
                state = "hung"
            try:
                client.update(
                    "bot_instances",
                    {"last_heartbeat_at": datetime.now(timezone.utc).isoformat(),
                     "status": state},
                    filters={"id": f"eq.{bot_id}"},
                )
            except Exception:
                pass  # a missed heartbeat shows up as 'stale', which is accurate
            if learning_enabled:
                try:
                    import app.learning as _learning

                    _learning.publish_status(client, bot_instance_id=bot_id, owner_id=owner_id)
                except Exception:  # noqa: BLE001 - the view shows a stale row, which is accurate
                    pass

    threading.Thread(target=beat, daemon=True, name="heartbeat").start()


# Off the boot path. This makes four to six control-plane calls at up to thirty
# seconds each, and it ran synchronously before freqtrade started -- so a slow
# Supabase was minutes of nothing serving, which the supervisor then had to
# outwait. Registration already retries from the heartbeat; nothing here needs
# to happen before the port answers.
threading.Thread(target=register_and_heartbeat, daemon=True, name="registration").start()

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

    # freqtrade constructs itself before the port answers, and assigns its
    # initial state only after the pairlist refresh (freqtradebot.py:148). A
    # /start that lands before that line is overwritten by it -- observed
    # 2026-09-16 05:53 -- so wait for the whole constructor, not for the port.
    # Unbounded, and logged: the give-up `return` that used to sit here ended
    # the supervisor for the life of the process whenever a boot took longer
    # than two minutes, which a slow database makes routine.
    waited = 0
    while not _bot_ready.wait(30):
        waited += 30
        if _stood_down.is_set():
            return
        print(f"still waiting for freqtrade to finish starting ({waited}s)", flush=True)
    while not _stood_down.is_set():
        try:
            local("ping")
            break
        except Exception:  # noqa: BLE001 - it is simply not up yet
            time.sleep(1)

    # Taking the lock needs its own database connection, so it fails when the
    # database is unreachable -- and this ran once, in a daemon thread, with no
    # handler. On 2026-09-08 Supabase's pooler answered ECHECKOUTTIMEOUT, the
    # exception killed this thread without a line in the log, and the bot sat in
    # its boot state -- STOPPED, alive, heartbeating, holding two positions and
    # managing neither -- for seven hours. Retried until it works.
    while not _stood_down.is_set():
        try:
            if acquire_trading_lock(db_url, bot_name, wait_seconds=300):
                break
            print("TRADING LOCK NOT ACQUIRED: staying stopped rather than running a "
                  "second trading process against one database. Retrying.", flush=True)
        except Exception as exc:  # noqa: BLE001 - the retry is the whole point
            print(f"could not take the trading lock ({exc}); retrying in "
                  f"{LOCK_RETRY_SECONDS}s", flush=True)
        time.sleep(LOCK_RETRY_SECONDS)
    else:
        return

    _ensure_trading(local, first=True)

    # Still watched, as a fallback: if the platform ever leaves two instances
    # up, the older one gives way rather than both sitting on one database.
    watch_for_takeover(bot_name, lambda reason: stand_down(reason, local))

    # And keep watching this one. Everything above happens once at boot, which
    # is exactly when the infrastructure is least settled; a bot that is up and
    # not trading is the state this whole system exists to prevent, so it is
    # also the one worth rechecking rather than assuming.
    while not _stood_down.is_set():
        time.sleep(SUPERVISE_SECONDS)
        _ensure_trading(local)
        # Only a trader that is verifiably trading gets to say so. Stopped,
        # hung, or unable to start all fall silent, and silence is the alarm.
        if _trader_state() == "running" and not _loop_is_stalled():
            _ping_heartbeat()


def _ensure_trading(local, first=False):
    """Start the trader if it should be running and is not.

    Deliberately reads the desired state every time rather than trusting what
    was wanted at boot: pressing Stop must not be undone a minute later by a
    supervisor that only remembers what it was told once.
    """
    try:
        wanted = _desired_state()
    except Exception as exc:  # noqa: BLE001
        print(f"could not read desired state ({exc}); leaving the trader alone",
              flush=True)
        return

    # In-process first: freqtrade's own view of itself, with no HTTP in the
    # way. The API route stays for a process that has no handle on the bot.
    state = _trader_state()
    if state is None:
        try:
            state = str((local("show_config") or {}).get("state") or "").lower()
        except Exception as exc:  # noqa: BLE001
            print(f"could not read the trader's state ({exc})", flush=True)
            return
        if not state:
            # The port answers before the RPC is attached. A start now is lost.
            print("freqtrade is still starting; leaving it alone", flush=True)
            return

    if wanted != "running":
        if first:
            print(f"lock held; staying {wanted} as asked", flush=True)
        else:
            print(f"trader is {state}; staying {wanted} as asked", flush=True)
        return

    if state == "running":
        return

    for attempt in range(1, START_ATTEMPTS + 1):
        after = _set_trader_state("running")
        if after is None:
            try:
                after = local("start", "POST").get("status")
            except Exception as exc:  # noqa: BLE001
                print(f"could not start the trader: {exc}", flush=True)
                return
        # freqtrade may still be assigning its initial state, which overwrites
        # this one. Look again before believing it.
        time.sleep(START_SETTLE_SECONDS)
        now = _trader_state()
        if now is None:
            try:
                now = str((local("show_config") or {}).get("state") or "").lower()
            except Exception:  # noqa: BLE001
                now = ""
        if now == "running":
            print(f"trader was {state or 'not running'}; started it ({after})", flush=True)
            return
        print(f"start did not stick (state {now!r}); attempt {attempt} of {START_ATTEMPTS}",
              flush=True)
    print("could not start the trader: it will not stay running", flush=True)
    _ping_heartbeat("/fail")


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

#: Engine pool for freqtrade's SQLAlchemy engine. Two scoped sessions on one
#: engine (trades, and custom data), every API request with a session of its
#: own, and a rolling deploy doubling all of it: 5 + 5 per instance is a third
#: of the default 15, and enough. Exhaustion surfaces as a retry in the loop
#: wrapper below, not as a thirty-second freeze.
DB_POOL_SIZE = 5
DB_POOL_OVERFLOW = 5
DB_POOL_TIMEOUT = 20

#: Waits between attempts to open freqtrade's first connection at boot. That
#: first connection is the one pool_pre_ping cannot help with, and it used to
#: be fatal on the first failure.
DB_BOOT_RETRY_WAITS = (5, 10, 20, 40, 60, 60)


def _is_transient_db_error(exc) -> bool:
    """Does this exception mean "the database is not there right now"?

    Connection dropped, connection refused, pool exhausted, a dead connection
    handed out: yes. A constraint violation, bad data, a programming error:
    no -- those are bugs and must stay fatal.
    """
    import sqlalchemy.exc as sa

    transient = [sa.OperationalError, sa.InterfaceError, sa.TimeoutError]
    try:
        import psycopg2

        transient += [psycopg2.OperationalError, psycopg2.InterfaceError]
    except ImportError:
        pass
    if isinstance(exc, tuple(transient)):
        return True
    return isinstance(exc, sa.DBAPIError) and bool(getattr(exc, "connection_invalidated", False))


def _make_the_db_connection_survivable() -> bool:
    """Give freqtrade's engine a health check and a bounded pool, and retry its
    first connection, since it does none of that itself.

    freqtrade creates its engine as `create_engine(db_url, future=True)` and
    only ever adds kwargs for sqlite, so a Postgres connection gets no
    liveness check at all. Supabase's pooler drops connections -- on its own
    maintenance, and on idle -- and the first query afterwards fails with
    "SSL SYSCALL error: EOF detected". freqtrade logs "Fatal exception!" and
    exits 1. That killed the live bot twice in ten days, mid-session, while it
    was holding open positions.

    pool_pre_ping makes SQLAlchemy check a pooled connection with a cheap
    round trip before handing it out, and transparently replace a dead one.
    The pool is bounded because the default (5 + 10 overflow, per instance,
    two instances during a deploy) was a plausible cause of the pooler's
    checkout timeouts, not just a victim of them. And init_db -- reflection,
    create_all, migrations, all on the very first connection -- is retried,
    because a pooler blip there is a crash loop the platform backs off to
    fifteen-minute intervals. Patched rather than vendored: freqtrade is a
    dependency and this survives upgrading it.
    """
    try:
        from freqtrade import persistence
        from freqtrade.persistence import models

        original_engine = models.create_engine

        def create_engine(url, **kwargs):
            kwargs.setdefault("pool_pre_ping", True)
            # Recycle well inside the pooler's own idle timeout, so connections
            # are replaced on our schedule rather than dropped on its.
            kwargs.setdefault("pool_recycle", 900)
            if str(url).startswith("postgresql"):
                # Only Postgres has a real pool; sqlite's StaticPool refuses these.
                kwargs.setdefault("pool_size", DB_POOL_SIZE)
                kwargs.setdefault("max_overflow", DB_POOL_OVERFLOW)
                kwargs.setdefault("pool_timeout", DB_POOL_TIMEOUT)
            return original_engine(url, **kwargs)

        models.create_engine = create_engine

        original_init_db = models.init_db

        def init_db(url):
            for attempt, wait in enumerate(DB_BOOT_RETRY_WAITS, 1):
                try:
                    return original_init_db(url)
                except Exception as exc:  # noqa: BLE001 - only the transient ones are retried
                    if not _is_transient_db_error(exc):
                        raise
                    print(f"database not ready for freqtrade (attempt {attempt} of "
                          f"{len(DB_BOOT_RETRY_WAITS) + 1}): {_first_line(exc)}; "
                          f"retrying in {wait}s", flush=True)
                    time.sleep(wait)
            return original_init_db(url)

        # Bound in two places: the module, and the package attribute that
        # freqtradebot imports from. Patching only the first changes nothing.
        models.init_db = init_db
        persistence.init_db = init_db
        return True
    except Exception as exc:  # noqa: BLE001 - better to trade without it than not at all
        print(f"WARNING: could not harden the database connection ({exc}); a dropped "
              "database connection will be fatal", flush=True)
        return False


def _discard_db_sessions():
    """Throw away the sessions that just failed, so the next pass starts clean.

    A session that raised mid-transaction is poisoned until rolled back, and
    the objects it loaded belong to the failed pass. remove() discards both;
    the next iteration re-queries. Both scoped sessions share the engine.
    """
    try:
        from freqtrade.persistence import Trade
        from freqtrade.persistence.custom_data import _CustomData

        sessions = (Trade.session, _CustomData.session)
    except Exception:  # noqa: BLE001 - not initialised yet; nothing to discard
        return
    for scoped in sessions:
        for step in ("rollback", "remove"):
            try:
                getattr(scoped, step)()
            except Exception:  # noqa: BLE001 - it is already broken; that is why we are here
                pass


def _make_the_trading_loop_survive_the_database() -> bool:
    """Turn a database error in the trading loop into a pause, not a death.

    freqtrade's worker catches only its own TemporaryError and
    OperationalException; anything else unwinds to main(), which logs "Fatal
    exception!" and exits 1. Every one of this bot's crashes was that path,
    with a pooler error at the bottom of it. Wrapped at Worker._worker rather
    than FreqtradeBot.process, because _worker is also where startup() runs
    after every /start and where process_stopped() runs -- both database-heavy,
    both outside process(), both fatal before this.

    Only errors that mean "the database is not there right now" are absorbed;
    a constraint violation or a programming error still ends the process. The
    poisoned sessions are discarded, the loop pauses for freqtrade's own retry
    interval, and the state is handed back unchanged so the next pass retries
    whatever transition was in flight. The same wrapper stamps the clock the
    heartbeat reads to tell a running loop from a hung one.
    """
    try:
        from freqtrade.constants import RETRY_TIMEOUT
        from freqtrade.worker import Worker

        original = Worker._worker

        def _worker(self, old_state):
            global _last_loop_at
            _last_loop_at = time.monotonic()
            try:
                return original(self, old_state)
            except Exception as exc:  # noqa: BLE001 - re-raised unless the database is away
                if not _is_transient_db_error(exc):
                    raise
                _discard_db_sessions()
                print(f"database unavailable mid-loop ({type(exc).__name__}: "
                      f"{_first_line(exc)}); pausing {RETRY_TIMEOUT}s and carrying on, "
                      "state unchanged", flush=True)
                time.sleep(RETRY_TIMEOUT)
                return old_state

        Worker._worker = _worker
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not make the trading loop survive database errors "
              f"({exc}); a dropped connection mid-loop will be fatal", flush=True)
        return False


def _get_a_handle_on_the_bot() -> bool:
    """Keep the FreqtradeBot instance, and know the moment it is ready.

    freqtrade's constructor starts the API server, refreshes the pairlist, and
    only then assigns the initial state. A /start that arrives over HTTP in
    between is overwritten -- and from outside, that window is indistinguishable
    from a bot that is genuinely stopped. With the instance in hand the
    supervisor sets the state directly, exactly as /start does from the API
    thread, and waits on an event that fires only after the constructor has
    returned. Re-armed on every construction, so a config reload is handled.
    """
    try:
        from freqtrade.freqtradebot import FreqtradeBot

        original = FreqtradeBot.__init__

        def __init__(self, config):
            _bot_ready.clear()
            original(self, config)
            _bot_holder["bot"] = self
            _bot_ready.set()

        FreqtradeBot.__init__ = __init__
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: could not get a handle on the bot ({exc}); the supervisor "
              "will use the API instead", flush=True)
        return False


if db_url:
    # Order matters: the database patch must land before freqtrade.worker is
    # imported, because importing it imports freqtradebot, which binds init_db.
    if _make_the_db_connection_survivable():
        print(f"database: pool_pre_ping on, pool {DB_POOL_SIZE}+{DB_POOL_OVERFLOW}, "
              "connections recycled every 15m, first connection retried", flush=True)
    if _make_the_trading_loop_survive_the_database():
        print("database: errors in the trading loop pause it instead of ending it", flush=True)
_get_a_handle_on_the_bot()

if db_url and learning_enabled:
    # Here and not earlier: the adapter imports freqtrade's classes to wrap
    # them, and importing those before the patch above would bind init_db
    # before it was made retryable. Nothing in this block can stop the boot.
    try:
        import app.learning as learning

        _learning_client = None
        try:
            from app.core.supabase import SupabaseClient

            _learning_client = SupabaseClient.service(timeout=15)
        except Exception as exc:  # noqa: BLE001 - records queue locally until there is one
            print(f"learning: no Supabase client ({exc}); recording locally only", flush=True)
        learning.install(
            _learning_client, outbox_path=learning_outbox_path, interval=learning_write_interval,
            identity=learning_identity, environment=environment, exchange=exchange_name,
            strategy_id=strategy, bot_name=bot_name, stake_currency=config["stake_currency"],
            dry_run=dry_run,
        )
        print(f"learning: recording trading decisions to {learning_outbox_path}"
              + ("" if _learning_client else " (writer idle until a service key is set)"), flush=True)
    except Exception as exc:  # noqa: BLE001 - the module must never cost a boot
        print(f"WARNING: learning module not installed ({exc}); trading is unaffected", flush=True)

from freqtrade.main import main as freqtrade_main

# freqtrade's main() exits the interpreter itself, so the old wrapper that
# handed its return value to sys.exit never ran -- and neither, sometimes, did
# the exit. The API server runs on a non-daemon thread that only
# ApiServer.cleanup() stops, and cleanup is skipped on the paths a "Fatal
# exception!" takes, so sys.exit waited on that thread forever: a dead trader
# with a port that still answered and a heartbeat still saying "running".
# os._exit does not wait for anyone.
code = 0
try:
    freqtrade_main(argv)
except SystemExit as exc:
    code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
except BaseException:  # noqa: BLE001 - it is over either way; say why on the way out
    import traceback

    traceback.print_exc()
    code = 1
finally:
    try:
        import app.learning as _learning

        _learning.flush(10.0)
    except Exception:  # noqa: BLE001 - the outbox file keeps what did not go
        pass
    print(f"freqtrade has finished; exiting {code}", flush=True)
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)
