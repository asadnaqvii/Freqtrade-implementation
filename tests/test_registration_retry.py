"""Registration failing once must not blind the bot for the rest of its life.

Supabase restricted this project for exceeding its egress quota on 2026-09-05
and answered 402 to every API call. freqtrade never noticed -- it talks to
Postgres directly -- so the bot went on trading. But registration failed, and
bot_id stayed None, and the heartbeat thread returned on its first tick.

The dashboard then showed the bot offline for 31 hours, through three restarts
and well past the point where the quota problem was fixed, while it was in fact
trading the whole time. Worse than a wrong number: a number that says the thing
you care about is dead when it is alive teaches you to stop believing it.

The loop is compiled out of render_start rather than imported, since importing
configures and launches a bot.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "render_start.py").read_text()


def test_the_heartbeat_retries_registration_rather_than_returning():
    beat = SOURCE[SOURCE.index("    def beat():"):]
    beat = beat[:beat.index("threading.Thread(target=beat")]

    assert "bot_id = register()" in beat, (
        "the heartbeat must be able to register; without this a failure at boot "
        "is permanent for the life of the process"
    )
    assert "continue" in beat, "a failed retry must keep the loop alive"
    # The old shape: give up entirely the first time bot_id is missing.
    assert "if not bot_id:\n                return" not in beat


def test_registration_is_a_function_so_it_can_be_called_again():
    assert "    def register():" in SOURCE
    assert "bot_id = register()" in SOURCE


def test_registration_failure_returns_none_rather_than_raising():
    """Boot must survive it. The bot trades from Postgres; the Supabase row is
    bookkeeping, and bookkeeping must never stop the trading."""
    register = SOURCE[SOURCE.index("    def register():"):]
    register = register[:register.index("    bot_id = register()")]
    assert "return None" in register
    assert "except Exception" in register


def test_a_stood_down_process_does_not_register_itself_back():
    """The retry runs on a loop that a standing-down instance also uses. If the
    stand-down check came after it, an outgoing process would put its own row
    back over its replacement's on the way out."""
    beat = SOURCE[SOURCE.index("    def beat():"):]
    beat = beat[:beat.index("threading.Thread(target=beat")]
    assert beat.index("_stood_down.is_set()") < beat.index("bot_id = register()")


def test_the_deployment_record_is_written_when_registration_finally_lands():
    """Otherwise a bot that registered late has no deployment row, and every
    trade it makes falls outside any strategy's window in the history."""
    beat = SOURCE[SOURCE.index("    def beat():"):]
    beat = beat[:beat.index("threading.Thread(target=beat")]
    assert "_record_deployment(client, bot_id, owner_id)" in beat
