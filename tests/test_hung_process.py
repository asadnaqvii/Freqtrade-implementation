"""A hung process must not look alive.

freqtrade's API server runs on a non-daemon thread that only
ApiServer.cleanup() stops, and cleanup is skipped on the paths a "Fatal
exception!" takes. The interpreter then waits on that thread forever: the port
answers, show_config reports the last in-memory state, the heartbeat writes
"running", and nothing trades. That is the one shape of failure every
dashboard calls healthy.

Two answers. The heartbeat reports "hung" when the trading loop has stopped
going round, whatever the port says. And the process exits with os._exit,
which waits for nobody, the moment freqtrade's main() returns.
"""

from __future__ import annotations

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "render_start.py").read_text()


def load_region():
    start = SOURCE.index("_trading_lock_conn = None")
    end = SOURCE.index("# --- database check ---")
    namespace = {"os": __import__("os"), "time": time, "threading": __import__("threading"),
                 "print": lambda *a, **k: None}
    exec(compile(SOURCE[start:end], "render_start.py", "exec"), namespace)
    return namespace


def test_a_trader_that_stops_looping_is_reported_as_hung_rather_than_running():
    ns = load_region()
    assert ns["_loop_is_stalled"]() is False, "before the loop has started nothing is hung"
    ns["_last_loop_at"] = time.monotonic() - 10
    assert ns["_loop_is_stalled"]() is False
    ns["_last_loop_at"] = time.monotonic() - ns["LOOP_STALL_SECONDS"] - 1
    assert ns["_loop_is_stalled"]() is True

    beat = SOURCE[SOURCE.index("    def beat():"):SOURCE.index('name="heartbeat"')]
    assert 'state = "hung"' in beat
    assert beat.index("_loop_is_stalled()") < beat.index('client.update(')


def test_the_stall_threshold_leaves_room_for_freqtrades_own_pauses():
    """One pass over the whitelist, freqtrade's 30 s pause on a temporary
    error, and the 30 s pause the loop wrapper adds all have to fit."""
    ns = load_region()
    assert 120 <= ns["LOOP_STALL_SECONDS"] <= 600


def test_the_process_exits_even_when_a_server_thread_will_not():
    tail = SOURCE[SOURCE.index("from freqtrade.main import main as freqtrade_main"):]
    assert "sys.exit(freqtrade_main" not in SOURCE, "main() exits itself; that line never ran"
    assert "freqtrade_main(argv)" in tail
    assert "finally:" in tail
    assert tail.index("finally:") < tail.index("os._exit(code)")


def run_tail(main_behaviour):
    tail = SOURCE[SOURCE.index("code = 0\ntry:\n    freqtrade_main(argv)"):]
    exits = []
    namespace = {
        "freqtrade_main": main_behaviour,
        "argv": ["trade"],
        "os": types.SimpleNamespace(_exit=exits.append),
        "sys": types.SimpleNamespace(stdout=sys.stdout, stderr=sys.stderr),
        "print": lambda *a, **k: None,
    }
    exec(compile(tail, "render_start.py", "exec"), namespace)
    return exits


def test_the_exit_code_survives_the_hard_exit():
    def exits_one(argv):
        raise SystemExit(1)

    def exits_clean(argv):
        raise SystemExit(None)

    def exits_with_a_message(argv):
        raise SystemExit("bad config")

    def returns(argv):
        return None

    assert run_tail(exits_one) == [1]
    assert run_tail(exits_clean) == [0]
    assert run_tail(exits_with_a_message) == [1]
    assert run_tail(returns) == [0]


def test_an_unexpected_exception_still_exits_non_zero():
    def blows_up(argv):
        raise RuntimeError("something freqtrade did not catch")

    assert run_tail(blows_up) == [1]
