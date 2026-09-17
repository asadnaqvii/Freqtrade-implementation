"""The Learning Module: an append-only record of what the bot saw, wanted,
did, and why -- separate from Verification, which proves what happened on
the exchange.

Nothing here trades, and nothing here can stop the bot trading: every write
goes through a local outbox and a background writer, and every hook the
freqtrade adapter installs swallows its own failures.

`start()` is the only thing render_start needs at boot; `flush()` runs on the
way out; `health()` is what the dashboard reads. The accessors are named
`current_*` rather than `outbox()`/`writer()` because those are also the
submodules, and importing a submodule rebinds the package attribute of the
same name.
"""

from __future__ import annotations

import threading

_state: dict = {"outbox": None, "writer": None}
_lock = threading.Lock()


def start(outbox_path: str, client, *, interval: float = 2.0):
    """Open the outbox and start the writer. Idempotent; returns the writer."""
    from app.learning.outbox import SqliteOutbox
    from app.learning.writer import LearningWriter

    with _lock:
        if _state["writer"] is not None:
            return _state["writer"]
        outbox = SqliteOutbox(outbox_path)
        writer = LearningWriter(outbox, client, interval=interval)
        writer.start()
        _state["outbox"], _state["writer"] = outbox, writer
        return writer


def current_outbox():
    return _state["outbox"]


def current_writer():
    return _state["writer"]


def flush(timeout: float = 10.0) -> bool:
    """Ship whatever is queued before the process goes away. Never raises."""
    w = _state["writer"]
    if w is None:
        return True
    try:
        return w.flush(timeout=timeout)
    except Exception:  # noqa: BLE001 - we are leaving; nothing to do about it
        return False


def health() -> dict:
    w = _state["writer"]
    if w is None:
        return {"enabled": False}
    return {"enabled": True, **w.health()}
