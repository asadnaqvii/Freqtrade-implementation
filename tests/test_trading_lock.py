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
    incumbent["watch_for_takeover"]("bot", lambda: yielded.append(True), poll_seconds=0.02)

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
    ns["watch_for_takeover"]("bot", lambda: called.append(True), poll_seconds=0.02)
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
