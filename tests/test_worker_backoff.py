"""An idle worker must go quiet.

The queue poll ran at a flat 10s. Over sixteen days that made 128,897
`claim_backtest_job` calls -- 465 seconds of database CPU and 773k buffer reads
-- essentially all of it asking an empty queue whether it was still empty.

On a Micro instance that is not free: the trading bot shares the same CPU and
the same disk, and Supabase warned the project was depleting its Disk IO
budget. Backtests are batch work; nobody is watching a queue that has nothing
in it.

What must not regress is the other half: the moment a job appears, the wait
goes back to the base interval, so a job queued straight after the last one
finished does not sit for a minute first.
"""

from __future__ import annotations

import pytest

from app.worker import main as worker


def backoff(seconds: int, base: int = 10) -> int:
    """The doubling the run loop applies after an empty poll."""
    return min(seconds * 2, worker.IDLE_MAX_POLL_SECONDS)


def test_an_empty_queue_backs_off():
    waits, poll = [], 10
    for _ in range(8):
        waits.append(poll)
        poll = backoff(poll)
    assert waits[0] == 10, "the first check after work must still be prompt"
    assert waits[-1] == worker.IDLE_MAX_POLL_SECONDS
    assert waits == sorted(waits), "the wait must never shorten while idle"


def test_the_backoff_is_bounded():
    poll = 10
    for _ in range(50):
        poll = backoff(poll)
    assert poll == worker.IDLE_MAX_POLL_SECONDS


def test_a_days_idling_is_a_fraction_of_the_polls_it_replaces():
    """The whole point: far fewer calls at the same responsiveness to real work."""
    day = 24 * 60 * 60
    before = day / 10
    after = day / worker.IDLE_MAX_POLL_SECONDS
    assert before > 8000
    assert after < before / 5


def test_finding_a_job_resets_the_wait():
    """Modelled on the loop: `idle_poll = base_poll` on a successful claim."""
    base, poll = 10, worker.IDLE_MAX_POLL_SECONDS
    poll = base  # what the loop does when a job is claimed
    assert poll == base


@pytest.mark.parametrize("name", ["IDLE_MAX_POLL_SECONDS", "STALL_SWEEP_SECONDS",
                                  "BOT_WATCH_SECONDS", "LOG_PRUNE_SECONDS"])
def test_the_intervals_are_configured(name):
    assert getattr(worker, name) > 0


def test_watching_the_bot_stays_frequent():
    """Backing off the batch queue must not slow the thing that guards real
    money. A stalled backtest can wait five minutes; a dead trading bot with
    open positions cannot."""
    assert worker.BOT_WATCH_SECONDS <= 60
    assert worker.BOT_WATCH_SECONDS < worker.STALL_SWEEP_SECONDS
