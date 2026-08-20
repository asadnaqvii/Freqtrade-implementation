"""A stop has to survive a restart.

freqtrade keeps running/stopped in memory: `_rpc_stop` sets an attribute and
nothing writes it down. render_start.py used to hardcode initial_state="running",
so a redeploy -- which Render does on its own, not only when someone pushes --
restarted a bot that had been deliberately stopped, with a live strategy and real
money. The dashboard now records what was asked for and the bot reads it back.
"""

from __future__ import annotations

import pytest

from app.api.routers.live import CONTROL_STATE, _record_intent


class DB:
    def __init__(self, metadata=None, boom=None):
        self.metadata = metadata
        self.boom = boom
        self.written = None

    def select_one(self, table, *, columns="*", filters=None):
        if self.boom:
            raise self.boom
        return {"metadata": self.metadata} if self.metadata is not None else None

    def update(self, table, values, *, filters=None):
        if self.boom:
            raise self.boom
        self.written = (table, values, dict(filters or {}))
        return [values]


BOT = {"id": "b1", "name": "mine"}


@pytest.mark.parametrize("action,state", [
    ("stop", "stopped"), ("start", "running"), ("stopentry", "paused"),
])
def test_each_control_records_the_state_it_leaves_the_bot_in(action, state):
    db = DB(metadata={})
    _record_intent(db, BOT, action)
    table, values, filters = db.written
    assert table == "bot_instances"
    assert values["metadata"]["desired_state"] == state
    assert filters == {"id": "eq.b1"}, "it must scope the write to this bot"


def test_reload_config_records_nothing():
    """It changes settings, not whether the bot trades."""
    db = DB(metadata={})
    _record_intent(db, BOT, "reload_config")
    assert db.written is None


def test_the_rest_of_the_metadata_survives():
    db = DB(metadata={"notes": "keep me", "desired_state": "running"})
    _record_intent(db, BOT, "stop")
    _, values, _ = db.written
    assert values["metadata"] == {"notes": "keep me", "desired_state": "stopped"}


def test_a_write_failure_does_not_undo_a_successful_stop():
    # The bot has already accepted the command by this point. Raising here would
    # report failure for something that did happen.
    _record_intent(DB(boom=RuntimeError("postgrest down")), BOT, "stop")


def test_a_bot_with_no_metadata_yet_still_records():
    db = DB(metadata=None)
    _record_intent(db, BOT, "stop")
    assert db.written[1]["metadata"] == {"desired_state": "stopped"}


def test_every_control_that_changes_state_is_covered():
    """If a control is added to the router, it has to be classified here too."""
    import re

    from app.api.routers.live import ControlRequest

    pattern = ControlRequest.model_fields["action"].metadata[0].pattern
    actions = set(re.fullmatch(r"\^\((.*)\)\$", pattern).group(1).split("|"))
    unclassified = actions - set(CONTROL_STATE) - {"reload_config"}
    assert not unclassified, f"these change the bot's state and are not recorded: {unclassified}"


def test_only_states_freqtrade_accepts_are_ever_written():
    # freqtrade's config schema enumerates exactly these; anything else makes the
    # config invalid and the bot refuses to boot at all.
    assert set(CONTROL_STATE.values()) <= {"running", "paused", "stopped"}


# ---------------------------------------------------------------------------
# The boot half: render_start reads the note back
# ---------------------------------------------------------------------------

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_desired_state(monkeypatch, *, row=None, boom=None, env=None, dry_run=True):
    """Compile just _desired_state out of render_start.

    Importing the module would configure and launch a bot, so the one function
    is compiled on its own against a stubbed Supabase client.
    """
    source = (ROOT / "render_start.py").read_text()
    start = source.index("def _desired_state():")
    end = source.index("\n\n", source.index("return \"running\"", start))

    class Client:
        @staticmethod
        def service():
            if boom:
                raise boom
            return Client()

        def select_one(self, table, *, columns="*", filters=None):
            if boom:
                raise boom
            return row

    module = types.ModuleType("app.core.supabase")
    module.SupabaseClient = Client
    monkeypatch.setitem(sys.modules, "app.core.supabase", module)

    values = dict(env or {})
    namespace = {
        "_env": lambda name, default=None: values.get(name, default),
        "bot_name": "freqtrade-bot",
        "dry_run": dry_run,
        "print": lambda *a, **k: None,
    }
    exec(compile(source[start:end], "render_start.py", "exec"), namespace)
    return namespace["_desired_state"]


@pytest.mark.parametrize("state", ["running", "paused", "stopped"])
def test_the_bot_boots_into_the_state_the_dashboard_asked_for(monkeypatch, state):
    fn = load_desired_state(monkeypatch, row={"metadata": {"desired_state": state}, "trading_mode": "dry_run"})
    assert fn() == state


def test_a_bot_that_was_never_stopped_runs(monkeypatch):
    assert load_desired_state(monkeypatch, row={"metadata": {}, "trading_mode": "dry_run"})() == "running"
    assert load_desired_state(monkeypatch, row=None)() == "running"
    assert load_desired_state(monkeypatch, row={"metadata": None, "trading_mode": "dry_run"})() == "running"


def test_an_unreachable_control_plane_does_not_stop_the_bot_trading(monkeypatch):
    """It was deployed to trade. Failing closed here would be a silent outage."""
    fn = load_desired_state(monkeypatch, boom=RuntimeError("postgrest down"))
    assert fn() == "running"


def test_a_value_freqtrade_would_reject_is_ignored(monkeypatch):
    # An invalid initial_state fails config validation and the bot never boots,
    # so a corrupted row must not be able to keep it down.
    for junk in ["STOPPED", "halted", "", 7, None, {"a": 1}]:
        fn = load_desired_state(monkeypatch, row={"metadata": {"desired_state": junk}, "trading_mode": "dry_run"})
        assert fn() == "running", junk


def test_an_env_override_wins_over_the_database(monkeypatch):
    """The way back up when the dashboard is the thing that is broken."""
    fn = load_desired_state(monkeypatch,
                            row={"metadata": {"desired_state": "stopped"}, "trading_mode": "dry_run"},
                            env={"FREQTRADE_INITIAL_STATE": "running"})
    assert fn() == "running"


def test_a_junk_override_falls_through_to_the_database(monkeypatch):
    fn = load_desired_state(monkeypatch,
                            row={"metadata": {"desired_state": "stopped"}, "trading_mode": "dry_run"},
                            env={"FREQTRADE_INITIAL_STATE": "yes"})
    assert fn() == "stopped"


def test_switching_between_dry_run_and_live_comes_up_stopped(monkeypatch):
    """The boot worth refusing.

    freqtrade's trades table does not record which mode wrote a row. A live bot
    that inherits a dry run's open position will try to sell coins it never
    bought, fail, and retry -- so the mode change itself is the signal to hold,
    whatever the dashboard last asked for.
    """
    fn = load_desired_state(
        monkeypatch,
        row={"metadata": {"desired_state": "running"}, "trading_mode": "dry_run"},
        dry_run=False,
    )
    assert fn() == "stopped"


def test_it_holds_going_the_other_way_too(monkeypatch):
    # live -> dry_run leaves real open positions in a database a paper bot would
    # then "close" without touching the exchange.
    fn = load_desired_state(
        monkeypatch,
        row={"metadata": {"desired_state": "running"}, "trading_mode": "live"},
        dry_run=True,
    )
    assert fn() == "stopped"


@pytest.mark.parametrize("mode,dry", [("dry_run", True), ("live", False)])
def test_an_unchanged_mode_does_not_hold(monkeypatch, mode, dry):
    fn = load_desired_state(
        monkeypatch,
        row={"metadata": {"desired_state": "running"}, "trading_mode": mode},
        dry_run=dry,
    )
    assert fn() == "running"


def test_a_first_boot_with_no_recorded_mode_is_not_a_mode_change(monkeypatch):
    fn = load_desired_state(monkeypatch, row={"metadata": {}}, dry_run=False)
    assert fn() == "running"


def test_the_env_override_still_wins_over_the_mode_guard(monkeypatch):
    """Deliberate is deliberate: the way to say "yes, I know" without SQL."""
    fn = load_desired_state(
        monkeypatch,
        row={"metadata": {}, "trading_mode": "dry_run"},
        dry_run=False,
        env={"FREQTRADE_INITIAL_STATE": "running"},
    )
    assert fn() == "running"
