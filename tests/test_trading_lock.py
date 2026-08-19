"""Tests for the single-writer guard on live trading.

A rolling deploy overlapped two freqtrade processes against one database and
they opened two positions on the same pair in the same second -- trade ids 5 and
6, PIEVERSE/USDT, both at 15:37:59. Freqtrade tracks open pairs in memory, not
in the database, so neither process could see the other's position.

In dry-run that cost nothing. On real money it is double the intended size in a
pair the strategy wanted once.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def load_render_start(monkeypatch, psycopg2_stub):
    """Import render_start with its side effects disabled.

    The module configures and launches a bot at import time, so the parts that
    would do that are stubbed. Only the lock is under test.
    """
    monkeypatch.setitem(sys.modules, "psycopg2", psycopg2_stub)
    monkeypatch.setenv("DRY_RUN", "true")
    spec = importlib.util.spec_from_file_location("_rs_under_test", ROOT / "render_start.py")
    module = importlib.util.module_from_spec(spec)
    # Executing the module would start freqtrade. Pull out just the function by
    # compiling the source and running the definitions it needs.
    source = (ROOT / "render_start.py").read_text()
    start = source.index("_trading_lock_conn = None")
    end = source.index("def verify_database(")
    namespace = {"time": __import__("time"), "print": lambda *a, **k: None}
    exec(compile(source[start:end], "render_start.py", "exec"), namespace)
    return namespace


class Cursor:
    def __init__(self, answers):
        self.answers = answers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.sql, self.params = sql, params

    def fetchone(self):
        return (self.answers.pop(0),)


class Connection:
    def __init__(self, answers):
        self.answers = answers
        self.autocommit = False
        self.closed = False

    def cursor(self):
        return Cursor(self.answers)

    def close(self):
        self.closed = True


def stub(answers, connect_error=None):
    module = types.ModuleType("psycopg2")
    holder = {}

    def connect(*args, **kwargs):
        if connect_error:
            raise connect_error
        holder["conn"] = Connection(list(answers))
        return holder["conn"]

    module.connect = connect
    module._holder = holder
    return module


def test_the_lock_is_taken_when_free(monkeypatch):
    pg = stub([True])
    ns = load_render_start(monkeypatch, pg)
    assert ns["acquire_trading_lock"]("postgresql+psycopg2://x/y", "freqtrade-bot") is True
    # The connection must be kept: an advisory lock lives on its connection, and
    # letting it be garbage collected would release the lock while still trading.
    assert ns["_trading_lock_conn"] is pg._holder["conn"]


def test_a_second_instance_is_refused_rather_than_allowed_to_trade(monkeypatch):
    # Every poll says "still held", so the wait expires.
    pg = stub([False] * 200)
    ns = load_render_start(monkeypatch, pg)
    monkeypatch.setitem(ns, "time", _instant_clock())
    assert ns["acquire_trading_lock"]("postgresql://x/y", "freqtrade-bot", wait_seconds=20) is False
    assert pg._holder["conn"].closed, "the losing instance must not sit on a connection"


def test_it_waits_for_a_departing_instance_instead_of_failing_at_once(monkeypatch):
    # Held twice, then released -- the shape of a deploy handover.
    pg = stub([False, False, True])
    ns = load_render_start(monkeypatch, pg)
    monkeypatch.setitem(ns, "time", _instant_clock())
    assert ns["acquire_trading_lock"]("postgresql://x/y", "freqtrade-bot", wait_seconds=60) is True


def test_the_key_is_stable_and_bot_specific(monkeypatch):
    seen = {}

    def record(name):
        pg = stub([True])
        ns = load_render_start(monkeypatch, pg)

        class Recorder(Cursor):
            pass

        conn_answers = [True]
        original = pg.connect

        def connect(*a, **k):
            conn = original(*a, **k)
            real_cursor = conn.cursor

            def cursor():
                cur = real_cursor()
                seen.setdefault(name, []).append(cur)
                return cur

            conn.cursor = cursor
            return conn

        pg.connect = connect
        ns["acquire_trading_lock"]("postgresql://x/y", name)
        return seen[name][0].params[0]

    a1, a2, b = record("bot-a"), None, record("bot-b")
    seen.clear()
    a2 = record("bot-a")
    assert a1 == a2, "the same bot must take the same lock across restarts"
    assert a1 != b, "different bots must not block each other"


def test_a_database_that_will_not_connect_does_not_pretend_to_hold_a_lock(monkeypatch):
    pg = stub([], connect_error=RuntimeError("no route"))
    ns = load_render_start(monkeypatch, pg)
    assert ns["acquire_trading_lock"]("postgresql://x/y", "freqtrade-bot") is False


def _instant_clock():
    """A time module whose sleep does not sleep but does advance the clock."""
    clock = {"now": 1000.0}
    fake = types.SimpleNamespace()
    fake.time = lambda: clock["now"]
    fake.sleep = lambda s: clock.__setitem__("now", clock["now"] + s)
    return fake
