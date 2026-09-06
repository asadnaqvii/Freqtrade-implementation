"""Tests for the single-writer guard on live trading.

Two failures shaped this, one after the other.

First, a rolling deploy overlapped two freqtrade processes against one database
and they opened two positions on the same pair in the same second -- trade ids 5
and 6, PIEVERSE/USDT, both at 15:37:59. Freqtrade tracks open pairs in memory,
so neither could see the other's.

The fix for that caused the second: refusing to start without the lock made the
service undeployable. Render keeps the incumbent running until the replacement
is healthy, and the replacement could not become healthy while the incumbent
held the lock. Four deploys failed in a row.

So the incumbent has to stand down. These tests hold both properties at once:
never two writers, and never a deploy that cannot finish.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_lock_module(monkeypatch, psycopg2_stub):
    """Pull just the locking functions out of render_start.

    Importing the module would configure and launch a bot, so the source between
    the two markers is compiled on its own.
    """
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_stub)
    source = (ROOT / "render_start.py").read_text()
    start = source.index("_trading_lock_conn = None")
    end = source.index("def verify_database(")
    namespace = {
        "os": __import__("os"),
        "time": __import__("time"),
        "threading": __import__("threading"),
        "print": lambda *a, **k: None,
    }
    exec(compile(source[start:end], "render_start.py", "exec"), namespace)
    return namespace


class Postgres:
    """An advisory-lock server shared by every connection in a test."""

    def __init__(self):
        self.held: dict[int, object] = {}

    def try_lock(self, key, owner):
        if key in self.held and self.held[key] is not owner:
            return False
        self.held[key] = owner
        return True

    def unlock(self, key, owner):
        if self.held.get(key) is owner:
            del self.held[key]

    def drop(self, owner):
        for key in [k for k, v in self.held.items() if v is owner]:
            del self.held[key]


class Cursor:
    def __init__(self, conn):
        self.conn = conn
        self.result = None

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        key = params[0]
        if "pg_try_advisory_lock" in sql:
            self.result = self.conn.server.try_lock(key, self.conn)
        elif "pg_advisory_unlock" in sql:
            self.conn.server.unlock(key, self.conn)
            self.result = True

    def fetchone(self):
        return (self.result,)


class Connection:
    def __init__(self, server):
        self.server = server
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def close(self):
        self.closed = True
        self.server.drop(self)


def stub(server, connect_error=None):
    module = types.ModuleType("psycopg2")
    module.connections = []

    def connect(*args, **kwargs):
        if connect_error:
            raise connect_error
        conn = Connection(server)
        module.connections.append(conn)
        return conn

    module.connect = connect
    return module


@pytest.fixture
def server():
    return Postgres()


# ---------------------------------------------------------------------------
# One writer
# ---------------------------------------------------------------------------

def test_the_first_instance_takes_the_lock(monkeypatch, server):
    ns = load_lock_module(monkeypatch, stub(server))
    assert ns["acquire_trading_lock"]("postgresql://x/y", "bot") is True
    # The connection must be kept: an advisory lock lives on it, and letting it
    # be collected would release the lock while still trading.
    assert ns["_trading_lock_conn"] is not None


def test_a_second_instance_does_not_get_it_while_the_first_holds_on(monkeypatch, server):
    first = load_lock_module(monkeypatch, stub(server))
    assert first["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    second = load_lock_module(monkeypatch, stub(server))
    second["time"] = _instant_clock()
    assert second["acquire_trading_lock"]("postgresql://x/y", "bot", wait_seconds=20) is False


def test_different_bots_do_not_block_each_other(monkeypatch, server):
    a = load_lock_module(monkeypatch, stub(server))
    b = load_lock_module(monkeypatch, stub(server))
    assert a["acquire_trading_lock"]("postgresql://x/y", "bot-a") is True
    assert b["acquire_trading_lock"]("postgresql://x/y", "bot-b") is True


# ---------------------------------------------------------------------------
# ...and a deploy that can still finish
# ---------------------------------------------------------------------------

def test_the_incumbent_is_asked_to_stand_down(monkeypatch, server):
    """The deadlock that made the service undeployable.

    A newcomer raises WANTED before waiting; the incumbent's watcher sees WANTED
    is no longer free and yields.
    """
    incumbent = load_lock_module(monkeypatch, stub(server))
    assert incumbent["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    yielded = []
    incumbent["watch_for_takeover"]("bot", lambda reason: yielded.append(reason), poll_seconds=0.02, grace_seconds=0)

    # Real clock on both sides: the handshake is a race between two threads, and
    # a fake clock on one of them removes the very interleaving under test.
    newcomer = load_lock_module(monkeypatch, stub(server))
    import threading

    result = {}
    t = threading.Thread(
        target=lambda: result.update(
            ok=newcomer["acquire_trading_lock"]("postgresql://x/y", "bot", wait_seconds=10)))
    t.start()

    import time as real_time

    for _ in range(200):
        if yielded:
            break
        real_time.sleep(0.02)

    assert yielded, "the incumbent never stood down; a deploy would hang here"

    # Standing down means the process exits, which drops its connection.
    incumbent["_trading_lock_conn"].close()
    t.join(timeout=10)
    assert result.get("ok") is True, "the replacement never got the lock"


def test_the_watcher_stops_when_the_lock_is_gone(monkeypatch, server):
    ns = load_lock_module(monkeypatch, stub(server))
    ns["_trading_lock_conn"] = None
    called = []
    ns["watch_for_takeover"]("bot", lambda: called.append(True), poll_seconds=0.02, grace_seconds=0)
    import time as real_time

    real_time.sleep(0.2)
    assert not called


def test_two_newcomers_do_not_fight(monkeypatch, server):
    """Only one replacement should wait; a second would raise WANTED forever."""
    incumbent = load_lock_module(monkeypatch, stub(server))
    incumbent["acquire_trading_lock"]("postgresql://x/y", "bot")

    a = load_lock_module(monkeypatch, stub(server))
    import threading

    threading.Thread(
        target=lambda: a["acquire_trading_lock"]("postgresql://x/y", "bot", wait_seconds=6),
        daemon=True).start()
    import time as real_time

    real_time.sleep(0.3)

    b = load_lock_module(monkeypatch, stub(server))
    b["time"] = _instant_clock()
    assert b["acquire_trading_lock"]("postgresql://x/y", "bot", wait_seconds=5) is False


def test_a_database_that_will_not_connect_does_not_pretend_to_hold_a_lock(monkeypatch, server):
    ns = load_lock_module(monkeypatch, stub(server, connect_error=RuntimeError("no route")))
    assert ns["acquire_trading_lock"]("postgresql://x/y", "bot") is False


def test_the_two_keys_are_different_and_stable():
    import hashlib

    def keys(name):
        d = hashlib.sha256(name.encode()).digest()
        return (int.from_bytes(d[:8], "big", signed=True),
                int.from_bytes(d[8:16], "big", signed=True))

    held, wanted = keys("freqtrade-bot")
    assert held != wanted, "one key would make wanting the lock the same as holding it"
    assert keys("freqtrade-bot") == (held, wanted)
    assert keys("other-bot")[0] != held


def _instant_clock():
    """A time module whose sleep advances a fake clock without waiting."""
    clock = {"now": 1000.0}
    fake = types.SimpleNamespace()
    fake.time = lambda: clock["now"]
    fake.sleep = lambda s: clock.__setitem__("now", clock["now"] + s)
    return fake


# ---------------------------------------------------------------------------
# The deploy that failed on 2026-08-20
# ---------------------------------------------------------------------------

def test_the_incumbents_own_probe_does_not_turn_a_newcomer_away(monkeypatch, server):
    """The bug that failed the live cutover deploy.

    The incumbent's watcher tests for waiters by taking WANTED and releasing it
    again, every few seconds. A newcomer that tried once and landed inside that
    window saw the incumbent's own probe, concluded a third instance was already
    queued, and exited -- so the deploy failed and the old instance kept running.

    Simulated by holding WANTED for one poll interval, which is exactly what the
    probe does.
    """
    incumbent = load_lock_module(monkeypatch, stub(server))
    assert incumbent["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    # Stand in for the probe: WANTED is taken right now, released shortly after.
    _, wanted_key = incumbent["_lock_keys"]("bot")
    probe = stub(server).connect()
    assert server.try_lock(wanted_key, probe) is True

    import threading
    import time as real_time

    newcomer = load_lock_module(monkeypatch, stub(server))
    result = {}
    t = threading.Thread(target=lambda: result.update(
        ok=newcomer["acquire_trading_lock"]("postgresql://x/y", "bot", wait_seconds=10)))
    t.start()

    real_time.sleep(1.5)          # longer than one announce retry
    server.unlock(wanted_key, probe)   # the probe releases, as it always does
    real_time.sleep(1.5)

    # The incumbent leaves, as it would once it noticed the newcomer.
    incumbent["_trading_lock_conn"].close()
    t.join(timeout=10)

    assert result.get("ok") is True, (
        "the newcomer gave up because the incumbent's own probe held WANTED for "
        "an instant; that is a failed deploy"
    )


def test_the_watcher_survives_a_dropped_query_and_still_stands_down(monkeypatch, server):
    """A watcher that has stopped watching can never stand down.

    It used to return on the first exception, permanently. Every deploy after
    that would wait out the full timeout and fail -- the undeployable state this
    whole handshake exists to prevent, reached by a different route.
    """
    incumbent = load_lock_module(monkeypatch, stub(server))
    assert incumbent["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    conn = incumbent["_trading_lock_conn"]
    real_cursor = conn.cursor
    failures = {"n": 0}

    def flaky():
        if failures["n"] < 2:
            failures["n"] += 1
            raise RuntimeError("connection reset by peer")
        return real_cursor()

    conn.cursor = flaky

    yielded = []
    incumbent["watch_for_takeover"]("bot", lambda reason: yielded.append(reason), poll_seconds=0.02, grace_seconds=0)

    import time as real_time

    real_time.sleep(0.2)
    assert failures["n"] == 2, "the watcher stopped polling after the first error"

    # Now a newcomer asks for the lock; the recovered watcher must notice.
    _, wanted_key = incumbent["_lock_keys"]("bot")
    waiter = stub(server).connect()
    assert server.try_lock(wanted_key, waiter) is True

    for _ in range(200):
        if yielded:
            break
        real_time.sleep(0.02)
    assert yielded, "the watcher never recovered, so the next deploy would fail"


# ---------------------------------------------------------------------------
# Serve first, trade second
# ---------------------------------------------------------------------------

def test_the_startup_serves_before_it_takes_the_lock():
    """The ordering that stops a deploy being marked failed.

    Render will not stop the incumbent until the replacement is healthy, and a
    private service is healthy when its port answers. So the port has to open
    before the lock is waited on -- otherwise the replacement waits for a lock
    the incumbent only releases once the replacement is healthy.

    Asserted against the source because the alternative is booting freqtrade.
    """
    source = (ROOT / "render_start.py").read_text()

    launch = source.index("from freqtrade.main import main as freqtrade_main")
    thread = source.index('name="trading-lock"')
    assert thread < launch, "the lock thread must be started before freqtrade blocks"

    body = source[source.index("def _take_lock_then_trade"):launch]
    assert body.index('local("ping")') < body.index("acquire_trading_lock"), (
        "it must wait for the port to answer before waiting on the lock"
    )
    assert body.index("acquire_trading_lock") < body.index('local(\'start\', \'POST\')'), (
        "it must hold the lock before it starts trading"
    )


def test_the_process_always_boots_stopped_when_a_lock_is_in_use():
    """Trading must not begin before the lock is held, whatever was asked for."""
    source = (ROOT / "render_start.py").read_text()
    tail = source[source.index("# Nothing above this line places an order"):]
    assert 'config["initial_state"] = "stopped"' in tail, (
        "booting straight into RUNNING would trade before the lock was taken"
    )


def _standing_down(monkeypatch, server, reason):
    """Run stand_down against a fake lock server and record what it did."""
    ns = load_lock_module(monkeypatch, stub(server))
    conn = stub(server).connect()
    ns["_trading_lock_conn"] = conn

    calls, exits = [], []
    ns["os"] = types.SimpleNamespace(_exit=lambda code: exits.append(code))
    ns["time"] = types.SimpleNamespace(sleep=lambda s: calls.append(("slept", s)))

    def local(path, method="GET"):
        calls.append((path, method))
        return {"status": "stopped"}

    ns["stand_down"](reason, local)
    return ns, conn, calls, exits


def test_a_handover_stops_trading_releases_the_lock_and_does_not_exit(monkeypatch):
    """The deploy path. Every step matters and the absent one matters most.

    Stopping trading first is what keeps this process off the market while the
    replacement takes over. Releasing the lock is what lets the replacement
    become healthy -- that is the deadlock this whole mechanism exists to break.
    Not exiting is what stops Render recording a failure and emailing about it.
    """
    server = Postgres()
    ns, conn, calls, exits = _standing_down(monkeypatch, server, "takeover")

    assert ("stop", "POST") in calls, "it kept trading while handing over"
    assert conn.closed, "the lock was not released; the replacement cannot start"
    assert ns["_stood_down"].is_set()

    # It waits to be terminated rather than ending itself. The only exit left is
    # the last resort, and it comes after the full grace -- so in production,
    # where the platform does stop us, it is never reached.
    assert calls[-1] == ("slept", ns["STANDDOWN_EXIT_AFTER"]), (
        "it must wait for the platform, not exit on its own"
    )
    assert exits in ([], [0]), "the handover path must not exit before waiting"


def test_losing_the_lock_connection_does_exit(monkeypatch):
    """The other ending, and it must stay different.

    Here nothing is waiting to replace us -- our own lock connection died. Going
    inert would leave the service up with nothing trading, so this one exits and
    the failure notice it sends is a true one.
    """
    server = Postgres()
    _, conn, calls, exits = _standing_down(monkeypatch, server, "lock_lost")

    assert ("stop", "POST") in calls, "it kept trading with no lock"
    assert exits[0] == 1, "a lost lock must end the process, not idle it"
    assert not any(c[0] == "slept" for c in calls), (
        "it must not idle for the grace period when nothing is coming to replace it"
    )


def test_the_lock_is_released_even_if_the_bot_will_not_stop(monkeypatch):
    """A replacement blocked on the lock is worse than an unstopped trader: the
    trader is about to be terminated anyway, the deploy is not."""
    server = Postgres()
    ns = load_lock_module(monkeypatch, stub(server))
    conn = stub(server).connect()
    ns["_trading_lock_conn"] = conn
    ns["os"] = types.SimpleNamespace(_exit=lambda code: None)
    ns["time"] = types.SimpleNamespace(sleep=lambda s: None)

    def local(path, method="GET"):
        raise RuntimeError("api not answering")

    ns["stand_down"]("takeover", local)
    assert conn.closed, "a failed stop must not strand the lock"


def test_the_watcher_says_which_ending_it_wants(monkeypatch):
    """The two endings are chosen by the caller, so the reason has to arrive."""
    server = Postgres()
    ns = load_lock_module(monkeypatch, stub(server))
    ns["_trading_lock_conn"] = stub(server).connect()

    _, wanted_key = ns["_lock_keys"]("bot")
    waiter = stub(server).connect()
    server.try_lock(wanted_key, waiter)

    seen = []
    ns["watch_for_takeover"]("bot", lambda reason: seen.append(reason), poll_seconds=0.02, grace_seconds=0)

    import time as real_time
    for _ in range(200):
        if seen:
            break
        real_time.sleep(0.02)
    assert seen == ["takeover"]


def test_a_dead_predecessors_flag_does_not_look_like_a_takeover(monkeypatch):
    """The 2026-09-05 outage, in one test.

    An OOM kill left the previous process's wanted-lock flag still held --
    Postgres does not reap a dead backend's advisory locks instantly. The
    replacement took the trading lock, saw the flag, read its own dead ancestor
    as a newcomer asking to take over, and stood down 67 seconds after
    starting: trading stopped, the heartbeat stopped with it, and a live
    process sat on four positions managing none of them, invisible to every
    dashboard. Twelve hours before anyone looked.

    Inside the boot grace a held flag must be ignored. No replacement for this
    process can exist that soon; the flag belongs to what it just replaced.
    """
    server = Postgres()
    ns = load_lock_module(monkeypatch, stub(server))
    ns["_trading_lock_conn"] = stub(server).connect()

    _, wanted_key = ns["_lock_keys"]("bot")
    corpse = stub(server).connect()          # the OOM-killed predecessor
    server.try_lock(wanted_key, corpse)      # whose flag is not yet reaped

    yielded = []
    ns["watch_for_takeover"]("bot", lambda reason: yielded.append(reason),
                             poll_seconds=0.02, grace_seconds=5)

    import time as real_time
    real_time.sleep(0.5)                     # many polls inside the grace
    assert yielded == [], "it stood down for a flag its own predecessor left"


def test_one_sighting_of_the_flag_is_not_enough(monkeypatch):
    """Past the grace, a single reading still is not proof. A real replacement
    holds the flag until it gets the lock, so it is there on the next poll too;
    a dying connection's is gone within seconds."""
    server = Postgres()
    ns = load_lock_module(monkeypatch, stub(server))
    ns["_trading_lock_conn"] = stub(server).connect()

    _, wanted_key = ns["_lock_keys"]("bot")
    corpse = stub(server).connect()
    server.try_lock(wanted_key, corpse)

    yielded = []
    ns["watch_for_takeover"]("bot", lambda reason: yielded.append(reason),
                             poll_seconds=0.05, grace_seconds=0, confirmations=3)

    import time as real_time
    real_time.sleep(0.06)                    # one poll only
    assert yielded == [], "one sighting was treated as a takeover"
    server.drop(corpse)                      # the flag is reaped
    real_time.sleep(0.3)
    assert yielded == [], "a flag that went away must not still stand it down"


def test_a_replacement_that_keeps_asking_is_still_obeyed(monkeypatch):
    """The confirmations must not become a way to ignore a real deploy. A
    replacement holds the flag until it gets the lock, so it survives every
    poll -- and the incumbent still has to give way, or the deploy hangs."""
    server = Postgres()
    ns = load_lock_module(monkeypatch, stub(server))
    ns["_trading_lock_conn"] = stub(server).connect()

    _, wanted_key = ns["_lock_keys"]("bot")
    replacement = stub(server).connect()
    server.try_lock(wanted_key, replacement)   # and keeps holding it

    yielded = []
    ns["watch_for_takeover"]("bot", lambda reason: yielded.append(reason),
                             poll_seconds=0.02, grace_seconds=0)

    import time as real_time
    for _ in range(200):
        if yielded:
            break
        real_time.sleep(0.02)
    assert yielded == ["takeover"], "a genuine deploy would hang forever"


def test_nothing_exits_on_the_normal_deploy_path():
    """A deliberate exit reads as a crash to the platform.

    Render classes any self-initiated exit as `earlyExit`, records
    `server_failed`, and emails "your service failed". Standing down used to
    exit, so every deploy sent a crash notice for a handover that went to plan
    -- 5 Sep 08:57:06, indistinguishable in the inbox from the real crashes on
    31 August and 4 September.
    """
    source = (ROOT / "render_start.py").read_text()
    body = source[source.index("def _take_lock_then_trade"):
                  source.index("from freqtrade.main import main as freqtrade_main")]
    assert "os._exit" not in body, "startup must not exit; the platform stops us"


# ---------------------------------------------------------------------------
# When the connection holding the lock dies
# ---------------------------------------------------------------------------

def test_losing_the_lock_connection_stops_this_process(monkeypatch, server):
    """The failure on 2026-08-31, and why tolerating it was wrong.

    Advisory locks live on the connection that took them. If that connection is
    gone the lock is already released server-side -- and this process is still
    trading, believing it holds it. Another instance can take the lock and open
    a position on the same pair, which is precisely the duplicate-trade failure
    this whole mechanism exists to prevent.

    Retrying a few times is right; a dropped packet is not a lost lock. Retrying
    forever is not.
    """
    ns = load_lock_module(monkeypatch, stub(server))
    assert ns["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    conn = ns["_trading_lock_conn"]

    def dead():
        raise RuntimeError("SSL SYSCALL error: EOF detected")

    conn.cursor = dead

    yielded = []
    ns["watch_for_takeover"]("bot", lambda reason: yielded.append(reason), poll_seconds=0.02, grace_seconds=0)

    import time as real_time

    for _ in range(200):
        if yielded:
            break
        real_time.sleep(0.02)

    assert yielded, (
        "a process whose lock connection is gone must stop, not keep trading "
        "while another instance is free to take the lock"
    )


def test_a_single_dropped_query_does_not_stop_trading(monkeypatch, server):
    """The other half: one bad packet is not a lost lock."""
    ns = load_lock_module(monkeypatch, stub(server))
    assert ns["acquire_trading_lock"]("postgresql://x/y", "bot") is True

    conn = ns["_trading_lock_conn"]
    real_cursor = conn.cursor
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("connection reset by peer")
        return real_cursor()

    conn.cursor = flaky

    yielded = []
    ns["watch_for_takeover"]("bot", lambda reason: yielded.append(reason), poll_seconds=0.02, grace_seconds=0)

    import time as real_time

    real_time.sleep(0.3)
    assert not yielded, "one transient error must not take a healthy bot down"
    assert calls["n"] > 1, "it must keep polling after a transient error"
