"""The bot survives its database.

Every crash this bot has had began in Supabase's pooler -- a dropped
connection, a checkout timeout, an authentication hiccup -- and ended the same
way: freqtrade catches none of it, main() logs "Fatal exception!", the process
exits, and the platform restarts it into whatever state the database is in
now. Five days of that, once.

Three properties fix it, all patched onto freqtrade at import time in
render_start.py and compiled out of it here against stub freqtrade modules
(importing render_start would launch a bot): the engine gets a health check and
a bounded pool; the first connection at boot is retried; and a database error
in the trading loop pauses the loop instead of ending the process -- while a
constraint violation, which is a bug, still does.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

import pytest
import sqlalchemy.exc as sa

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "render_start.py").read_text()


def stub_freqtrade(monkeypatch, *, init_db_errors=(), worker_error=None):
    """Stub the freqtrade modules the patches import. Returns what they touch."""
    pkg = types.ModuleType("freqtrade")
    persistence = types.ModuleType("freqtrade.persistence")
    models = types.ModuleType("freqtrade.persistence.models")
    custom_data = types.ModuleType("freqtrade.persistence.custom_data")
    worker = types.ModuleType("freqtrade.worker")
    constants = types.ModuleType("freqtrade.constants")
    constants.RETRY_TIMEOUT = 30

    engines = []

    def create_engine(url, **kwargs):
        engines.append((url, kwargs))
        return ("engine", url)

    models.create_engine = create_engine

    errors = list(init_db_errors)
    init_calls = []

    def init_db(url):
        init_calls.append(url)
        if errors:
            raise errors.pop(0)
        return "ok"

    models.init_db = init_db
    persistence.init_db = init_db
    persistence.models = models

    class Session:
        def __init__(self):
            self.calls = []

        def rollback(self):
            self.calls.append("rollback")

        def remove(self):
            self.calls.append("remove")

    class Trade:
        session = Session()

    class _CustomData:
        session = Session()

    persistence.Trade = Trade
    custom_data._CustomData = _CustomData

    class Worker:
        def __init__(self):
            self.calls = 0

        def _worker(self, old_state):
            self.calls += 1
            if worker_error is not None and self.calls == 1:
                raise worker_error
            return "RUNNING"

    worker.Worker = Worker
    pkg.persistence, pkg.worker, pkg.constants = persistence, worker, constants
    for name, module in {
        "freqtrade": pkg, "freqtrade.persistence": persistence,
        "freqtrade.persistence.models": models,
        "freqtrade.persistence.custom_data": custom_data,
        "freqtrade.worker": worker, "freqtrade.constants": constants,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return types.SimpleNamespace(engines=engines, init_calls=init_calls, models=models,
                                 persistence=persistence, Trade=Trade,
                                 CustomData=_CustomData, Worker=Worker)


def load_patches(sleeps=None):
    """Compile the patch functions and their constants out of render_start."""
    start = SOURCE.index("#: Engine pool for freqtrade's SQLAlchemy engine.")
    end = SOURCE.index("def _get_a_handle_on_the_bot")
    sleeps = [] if sleeps is None else sleeps
    namespace = {
        "time": types.SimpleNamespace(sleep=sleeps.append, monotonic=time.monotonic),
        "print": lambda *a, **k: None,
        "_first_line": lambda exc: str(exc),
        "_last_loop_at": None,
    }
    exec(compile(SOURCE[start:end], "render_start.py", "exec"), namespace)
    return namespace


def dropped(message="SSL SYSCALL error: EOF detected"):
    return sa.OperationalError("select 1", {}, Exception(message))


# ── the engine ─────────────────────────────────────────────────────────────

def test_the_engine_is_built_with_a_bounded_pool(monkeypatch):
    stub = stub_freqtrade(monkeypatch)
    ns = load_patches()
    assert ns["_make_the_db_connection_survivable"]() is True
    stub.models.create_engine("postgresql+psycopg2://u:p@h/db", future=True)
    [(url, kwargs)] = stub.engines
    assert kwargs["pool_pre_ping"] is True
    assert kwargs["pool_recycle"] == 900
    assert kwargs["pool_size"] == ns["DB_POOL_SIZE"]
    assert kwargs["max_overflow"] == ns["DB_POOL_OVERFLOW"]
    assert kwargs["pool_timeout"] == ns["DB_POOL_TIMEOUT"]
    assert kwargs["future"] is True, "freqtrade's own arguments must pass through"


def test_sqlite_gets_the_health_check_but_no_pool_arguments(monkeypatch):
    """sqlite's StaticPool refuses pool sizing; local runs must still start."""
    stub = stub_freqtrade(monkeypatch)
    load_patches()["_make_the_db_connection_survivable"]()
    stub.models.create_engine("sqlite:///user_data/tradesv3.sqlite")
    [(_, kwargs)] = stub.engines
    assert "pool_size" not in kwargs and "max_overflow" not in kwargs
    assert kwargs["pool_pre_ping"] is True


def test_the_pool_is_small_enough_for_two_instances_to_overlap():
    """A rolling deploy runs two instances on one database. The default pool
    (5 + 10 overflow) made that thirty connections, which is a plausible cause
    of the pooler's checkout timeouts and not just a victim of them."""
    ns = load_patches()
    assert ns["DB_POOL_SIZE"] + ns["DB_POOL_OVERFLOW"] <= 10


# ── the first connection ───────────────────────────────────────────────────

def test_the_first_connection_is_retried_before_the_bot_gives_up(monkeypatch):
    stub = stub_freqtrade(monkeypatch, init_db_errors=[dropped("ECHECKOUTTIMEOUT"),
                                                        dropped("ECHECKOUTTIMEOUT")])
    sleeps = []
    ns = load_patches(sleeps)
    ns["_make_the_db_connection_survivable"]()
    assert stub.persistence.init_db("postgresql://u:p@h/db") == "ok"
    assert len(stub.init_calls) == 3
    assert sleeps == list(ns["DB_BOOT_RETRY_WAITS"][:2])


def test_the_patched_first_connection_is_the_one_freqtradebot_imports(monkeypatch):
    """freqtradebot does `from freqtrade.persistence import init_db`, so the
    package attribute is the binding that matters; patching only the models
    module would change nothing."""
    stub = stub_freqtrade(monkeypatch)
    load_patches()["_make_the_db_connection_survivable"]()
    assert stub.persistence.init_db is stub.models.init_db
    assert stub.persistence.init_db.__name__ == "init_db"
    assert stub.persistence.init_db is not stub.init_calls  # sanity: a function


def test_a_url_that_will_never_work_is_not_retried_for_five_minutes(monkeypatch):
    stub = stub_freqtrade(monkeypatch, init_db_errors=[ValueError("no valid database URL")])
    sleeps = []
    load_patches(sleeps)["_make_the_db_connection_survivable"]()
    with pytest.raises(ValueError):
        stub.persistence.init_db("nonsense://")
    assert len(stub.init_calls) == 1
    assert sleeps == []


def test_a_bad_first_connection_eventually_raises_for_real(monkeypatch):
    ns = load_patches([])
    stub = stub_freqtrade(monkeypatch, init_db_errors=[dropped()] * (len(ns["DB_BOOT_RETRY_WAITS"]) + 1))
    ns["_make_the_db_connection_survivable"]()
    with pytest.raises(sa.OperationalError):
        stub.persistence.init_db("postgresql://u:p@h/db")
    assert len(stub.init_calls) == len(ns["DB_BOOT_RETRY_WAITS"]) + 1


# ── the trading loop ───────────────────────────────────────────────────────

def test_a_dropped_connection_pauses_the_loop_instead_of_killing_the_process(monkeypatch):
    stub = stub_freqtrade(monkeypatch, worker_error=dropped())
    sleeps = []
    ns = load_patches(sleeps)
    assert ns["_make_the_trading_loop_survive_the_database"]() is True
    worker = stub.Worker()
    assert stub.Worker._worker(worker, "RUNNING") == "RUNNING"
    assert sleeps == [30], "freqtrade's own retry interval"
    assert stub.Worker._worker(worker, "RUNNING") == "RUNNING"
    assert worker.calls == 2


def test_a_pool_that_hands_out_nothing_is_treated_like_a_dropped_connection(monkeypatch):
    """Pool exhaustion is sqlalchemy.exc.TimeoutError, which is not a
    DBAPIError -- catching only those would make it fatal again."""
    stub = stub_freqtrade(monkeypatch, worker_error=sa.TimeoutError("QueuePool limit reached"))
    ns = load_patches([])
    ns["_make_the_trading_loop_survive_the_database"]()
    assert stub.Worker._worker(stub.Worker(), "RUNNING") == "RUNNING"


def test_an_invalidated_connection_is_absorbed_and_a_plain_database_error_is_not(monkeypatch):
    ns = load_patches([])
    absorbed = sa.DBAPIError("select 1", {}, Exception("gone"), connection_invalidated=True)
    stub = stub_freqtrade(monkeypatch, worker_error=absorbed)
    ns["_make_the_trading_loop_survive_the_database"]()
    assert stub.Worker._worker(stub.Worker(), "RUNNING") == "RUNNING"

    ns = load_patches([])
    plain = sa.DBAPIError("select 1", {}, Exception("odd"))
    stub = stub_freqtrade(monkeypatch, worker_error=plain)
    ns["_make_the_trading_loop_survive_the_database"]()
    with pytest.raises(sa.DBAPIError):
        stub.Worker._worker(stub.Worker(), "RUNNING")


def test_a_constraint_violation_is_not_swallowed_as_a_blip(monkeypatch):
    """That is a bug, and a bot that keeps trading past a bug is worse than
    one that stops."""
    stub = stub_freqtrade(monkeypatch,
                          worker_error=sa.IntegrityError("insert", {}, Exception("duplicate key")))
    load_patches([])["_make_the_trading_loop_survive_the_database"]()
    with pytest.raises(sa.IntegrityError):
        stub.Worker._worker(stub.Worker(), "RUNNING")


def test_both_sessions_are_discarded_before_the_next_attempt(monkeypatch):
    """Two scoped sessions share the engine. A session that raised is poisoned
    until rolled back, and the objects it loaded belong to the failed pass."""
    stub = stub_freqtrade(monkeypatch, worker_error=dropped())
    load_patches([])["_make_the_trading_loop_survive_the_database"]()
    stub.Worker._worker(stub.Worker(), "RUNNING")
    assert stub.Trade.session.calls == ["rollback", "remove"]
    assert stub.CustomData.session.calls == ["rollback", "remove"]


def test_a_database_failure_while_starting_up_is_retried_rather_than_fatal(monkeypatch):
    """startup() runs inside _worker on every STOPPED -> RUNNING transition.
    The state is handed back unchanged so the transition is tried again."""
    stub = stub_freqtrade(monkeypatch, worker_error=dropped())
    load_patches([])["_make_the_trading_loop_survive_the_database"]()
    assert stub.Worker._worker(stub.Worker(), "STOPPED") == "STOPPED"


def test_the_loop_stamps_the_clock_each_time_round(monkeypatch):
    stub = stub_freqtrade(monkeypatch)
    ns = load_patches([])
    ns["_make_the_trading_loop_survive_the_database"]()
    assert ns["_last_loop_at"] is None
    stub.Worker._worker(stub.Worker(), "RUNNING")
    assert isinstance(ns["_last_loop_at"], float)
