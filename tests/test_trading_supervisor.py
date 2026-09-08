"""A bot that is up and not trading must not stay that way.

On 2026-09-08 Supabase's pooler answered ECHECKOUTTIMEOUT. freqtrade exited,
Render restarted it, and the bot came up in its boot state -- STOPPED, by
design, because the port has to answer before the trading lock is waited on.
The thread that takes the lock and then starts the trader raised on that same
unreachable database, died without logging a line, and nothing ever tried
again.

So the bot sat for seven hours: alive, heartbeating, reporting healthy, holding
two positions and managing the stop-loss on neither. Every dashboard was green.

Two properties fix that, and both are tested here. Taking the lock is retried
rather than attempted once. And having taken it, the trader is rechecked on a
timer, because boot is exactly when the infrastructure is least settled and a
one-shot start is a coin flip against it.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "render_start.py").read_text()
TAKE_LOCK = SOURCE[SOURCE.index("def _take_lock_then_trade"):
                   SOURCE.index("def _ensure_trading")]
_ENSURE_AT = SOURCE.index("def _ensure_trading")
ENSURE = SOURCE[_ENSURE_AT:SOURCE.index("\n\n", SOURCE.index("could not start the trader", _ENSURE_AT))]


def test_a_failed_lock_attempt_is_retried_not_fatal():
    """It ran once inside a daemon thread with no handler, so one exception
    ended it silently and permanently."""
    assert "while not _stood_down.is_set():" in TAKE_LOCK
    assert "except Exception" in TAKE_LOCK, "an unreachable database must not kill it"
    assert "LOCK_RETRY_SECONDS" in TAKE_LOCK


def test_the_lock_wait_is_short_enough_to_retry_within():
    """A 30 minute wait inside a retry loop is not a retry loop."""
    wait = re.search(r"acquire_trading_lock\(db_url, bot_name, wait_seconds=(\d+)\)", TAKE_LOCK)
    assert wait and int(wait.group(1)) <= 600


def test_the_trader_is_rechecked_on_a_timer():
    """The whole failure was a one-shot start that missed."""
    assert "SUPERVISE_SECONDS" in TAKE_LOCK
    assert TAKE_LOCK.count("_ensure_trading") >= 2, (
        "once at boot and again on a loop; one call is the bug this fixes"
    )


def test_the_supervisor_stops_when_the_process_stands_down():
    """Otherwise an instance handing over to its replacement would keep
    restarting a trader it no longer holds the lock for."""
    for block in (TAKE_LOCK,):
        assert "_stood_down.is_set()" in block


def test_stop_is_never_overridden_by_the_supervisor():
    """The most dangerous thing a supervisor can do is undo a deliberate Stop.
    The desired state is read on every pass, not remembered from boot."""
    assert "_desired_state()" in ENSURE
    assert 'wanted != "running"' in ENSURE
    assert ENSURE.index("_desired_state()") < ENSURE.index('local("start"'), (
        "it must decide whether it is wanted before it starts anything"
    )


def test_a_running_trader_is_left_alone():
    """Re-issuing start on a healthy bot every two minutes would be its own
    kind of broken."""
    assert 'if state == "running":' in ENSURE
    assert ENSURE.index('if state == "running":') < ENSURE.index('local("start"')


def test_an_unreadable_state_does_not_trigger_a_blind_start():
    """If the trader's state cannot be read, starting anyway risks acting on a
    bot that is mid-shutdown or already trading."""
    assert "could not read the trader's state" in ENSURE
    after = ENSURE[ENSURE.index("could not read the trader's state"):]
    assert "return" in after, "an unreadable state must stop, not fall through to start"
    assert after.index("return") < after.index('local("start"')


def test_the_supervisor_interval_is_shorter_than_a_trading_candle():
    """Four hours of not trading because the check runs every five would defeat
    the point."""
    interval = re.search(r"SUPERVISE_SECONDS = (\d+)", SOURCE)
    assert interval and int(interval.group(1)) <= 300
