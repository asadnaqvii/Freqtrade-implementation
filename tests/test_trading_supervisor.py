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


# ---------------------------------------------------------------------------
# Starting in-process, and never giving up on the boot
# ---------------------------------------------------------------------------

import types


def load_ensure(*, wanted="running", states=(), trader_none=False, local=None):
    """Compile _ensure_trading with a scripted trader.

    `states` is what _trader_state reports on each call; `_set_trader_state`
    is recorded rather than acted on, so a start that freqtrade overwrites
    (the boot race) is scripted by listing "stopped" again after it.
    """
    start = SOURCE.index("def _ensure_trading(local, first=False):")
    end = SOURCE.index("if db_url:\n    # Serve first, trade second.")
    reported = list(states)
    log, sets, pings, sleeps = [], [], [], []

    def trader_state():
        if trader_none:
            return None
        return reported.pop(0) if len(reported) > 1 else reported[0]

    namespace = {
        "print": lambda *a, **k: log.append(" ".join(str(x) for x in a)),
        "time": types.SimpleNamespace(sleep=sleeps.append),
        "START_ATTEMPTS": 3, "START_SETTLE_SECONDS": 5,
        "_desired_state": lambda: wanted,
        "_trader_state": trader_state,
        "_set_trader_state": lambda name: (sets.append(name), "running")[1],
        "_ping_heartbeat": lambda path="": pings.append(path),
    }
    exec(compile(SOURCE[start:end], "render_start.py", "exec"), namespace)
    return namespace["_ensure_trading"], types.SimpleNamespace(
        log=log, sets=sets, pings=pings, sleeps=sleeps, local=local)


def test_the_trader_is_started_in_process_once_freqtrade_has_finished_starting():
    """The constructor assigns the initial state after the pairlist refresh;
    a start delivered over HTTP before that line is overwritten by it."""
    assert "_bot_ready.wait(" in TAKE_LOCK
    assert TAKE_LOCK.index("_bot_ready.wait(") < TAKE_LOCK.index("acquire_trading_lock")
    assert '_set_trader_state("running")' in ENSURE
    assert ENSURE.index("_trader_state()") < ENSURE.index('local("show_config")'), (
        "freqtrade's own view of itself comes first; HTTP is the fallback"
    )


def test_a_start_issued_while_freqtrade_is_still_booting_is_not_lost():
    ensure, seen = load_ensure(states=["stopped", "stopped", "running"])
    ensure(local=None)
    assert seen.sets == ["running", "running"], "the first start was overwritten; it tried again"
    assert seen.sleeps == [5, 5]
    assert any("started it" in line for line in seen.log)
    assert seen.pings == []


def test_a_start_that_does_not_stick_is_tried_again_and_then_reported():
    ensure, seen = load_ensure(states=["stopped"])
    ensure(local=None)
    assert seen.sets == ["running"] * 3
    assert seen.pings == ["/fail"], "the dead-man's switch hears about a start that will not hold"
    assert any("will not stay running" in line for line in seen.log)


def test_a_running_trader_is_not_started_again():
    ensure, seen = load_ensure(states=["running"])
    ensure(local=None)
    assert seen.sets == []


def test_a_deliberate_stop_is_honoured_and_said_out_loud():
    """Silent returns are how a bot sits stopped for five days unnoticed."""
    ensure, seen = load_ensure(wanted="stopped", states=["stopped"])
    ensure(local=None)
    assert seen.sets == []
    assert any("staying stopped as asked" in line for line in seen.log)


def test_a_bot_still_starting_is_not_started_over_http():
    """Before the RPC is attached, show_config reports an empty state. A start
    then would be accepted and discarded."""
    calls = []

    def local(path, method="GET"):
        calls.append((path, method))
        return {"state": ""}

    ensure, seen = load_ensure(trader_none=True, states=[])
    ensure(local=local)
    assert ("start", "POST") not in calls
    assert any("still starting" in line for line in seen.log)


def test_a_slow_boot_does_not_end_the_supervisor():
    """The give-up `return` after 120 pings ended the supervisor for the life
    of the process whenever a boot took longer than two minutes."""
    assert "range(120)" not in TAKE_LOCK
    assert "never answered locally" not in TAKE_LOCK
    waiting = TAKE_LOCK[TAKE_LOCK.index("_bot_ready.wait("):TAKE_LOCK.index("acquire_trading_lock")]
    assert "still waiting for freqtrade" in waiting, "a slow boot is logged, not abandoned"


def test_the_dead_mans_switch_is_only_pinged_while_the_trader_is_verifiably_trading():
    loop = TAKE_LOCK[TAKE_LOCK.index("while not _stood_down.is_set():\n        time.sleep(SUPERVISE_SECONDS)"):]
    assert '_trader_state() == "running" and not _loop_is_stalled()' in loop
    assert loop.index("_loop_is_stalled()") < loop.index("_ping_heartbeat()")


def test_a_heartbeat_failure_never_raises(monkeypatch):
    import time as real_time
    import urllib.request

    start = SOURCE.index("_trading_lock_conn = None")
    end = SOURCE.index("# --- database check ---")
    ns = {"os": __import__("os"), "time": real_time, "threading": __import__("threading"),
          "print": lambda *a, **k: None}
    exec(compile(SOURCE[start:end], "render_start.py", "exec"), ns)

    ns["HEARTBEAT_URL"] = ""
    called = []
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: called.append(a))
    assert ns["_ping_heartbeat"]() is False and called == [], "no url, no network"

    ns["HEARTBEAT_URL"] = "https://hc-ping.com/abc"

    def refuse(request, timeout=0):
        raise OSError("network down")

    monkeypatch.setattr(urllib.request, "urlopen", refuse)
    assert ns["_ping_heartbeat"]("/fail") is False
