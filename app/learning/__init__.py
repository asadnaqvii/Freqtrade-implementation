"""The Learning Module: an append-only record of what the bot saw, wanted,
did, and why -- separate from Verification, which proves what happened on
the exchange.

Nothing here trades, and nothing here can stop the bot trading: every write
goes through a local outbox and a background writer, and every hook the
freqtrade adapter installs swallows its own failures.

`install()` is the one call render_start makes at boot; `flush()` runs on
the way out; `publish_status()` runs from the heartbeat; `health()` is what
the dashboard reads. The accessors are named `current_*` rather than
`outbox()`/`writer()` because those are also the submodules, and importing a
submodule rebinds the package attribute of the same name.
"""

from __future__ import annotations

import threading
import time

_state: dict = {"outbox": None, "writer": None, "recorder": None, "adapter": None}
_lock = threading.Lock()

#: How often the module says how it is doing in the log, for a deployment
#: with no Supabase client to publish to.
REPORT_EVERY_SECONDS = 600


def say(message: str) -> None:
    """The module's log line. Flushed: on Render stdout is a pipe, and a plain
    print sits in the buffer until it fills -- a ten-minute report that shows
    up an hour late, and an adapter error that never shows up at all."""
    print(message, flush=True)


def _open_outbox(outbox_path: str):
    from app.learning.outbox import SqliteOutbox

    if _state["outbox"] is None:
        _state["outbox"] = SqliteOutbox(outbox_path)
    return _state["outbox"]


def start(outbox_path: str, client, *, interval: float = 2.0):
    """Open the outbox and start the writer. Idempotent; returns the writer."""
    from app.learning.writer import LearningWriter

    with _lock:
        if _state["writer"] is not None:
            return _state["writer"]
        outbox = _open_outbox(outbox_path)
        writer = LearningWriter(outbox, client, interval=interval)
        writer.start()
        _state["writer"] = writer
        return writer


def install(client, *, outbox_path: str, identity: dict, environment: str, exchange: str,
            strategy_id: str, bot_name: str, stake_currency: str, dry_run: bool = False,
            interval: float = 2.0, log=say):
    """Start recording: the outbox, the writer (when there is a client), the
    recorder, and the freqtrade hooks. Idempotent; returns the recorder.

    Without a client -- no service key configured -- records still queue in
    the outbox and ship once a client exists on a later boot.
    """
    from app.learning import freqtrade_adapter, provenance
    from app.learning.recorder import Recorder

    with _lock:
        if _state["recorder"] is not None:
            return _state["recorder"]
        outbox = _open_outbox(outbox_path)
    if client is not None:
        start(outbox_path, client, interval=interval)
    with _lock:
        identity.setdefault("bot_name", bot_name)
        recorder = Recorder(
            outbox, identity, environment=environment, exchange=exchange, strategy_id=strategy_id,
            provenance={
                "strategy_code_hash": provenance.strategy_sha(strategy_id),
                "feature_set_version": provenance.FEATURE_SET_VERSION,
                "adapter_version": provenance.ADAPTER_VERSION,
                "environment": environment,
                "dry_run": bool(dry_run),
            },
            log=log,
        )
        adapter = freqtrade_adapter.install(recorder, bot_name=bot_name, stake_currency=stake_currency,
                                            on_cleanup=lambda: flush(10.0), log=log)
        _state["recorder"], _state["adapter"] = recorder, adapter
        threading.Thread(target=_report, args=(log,), daemon=True, name="learning-report").start()
        return recorder


def _report(log) -> None:
    while True:
        time.sleep(REPORT_EVERY_SECONDS)
        try:
            recorder, writer = _state["recorder"], _state["writer"]
            if recorder is None:
                return
            stats = recorder.health()
            queue = recorder.outbox.stats()
            log(f"learning: {stats['decisions_opened']} decisions and {stats['events_recorded']} events "
                f"recorded since boot ({stats['decisions_deduped']} repeats folded, "
                f"{stats['adapter_errors']} adapter errors); outbox pending {queue.get('outbox_pending')}, "
                f"quarantined {queue.get('outbox_quarantined')}; writer "
                f"{'alive' if writer is not None and writer.is_alive() else 'absent (no service key)'}")
        except Exception:  # noqa: BLE001 - a report that fails is not worth a thread
            pass


def current_outbox():
    return _state["outbox"]


def current_writer():
    return _state["writer"]


def current_recorder():
    return _state["recorder"]


def flush(timeout: float = 10.0) -> bool:
    """Ship whatever is queued before the process goes away. Never raises."""
    w = _state["writer"]
    if w is None:
        return True
    try:
        return w.flush(timeout=timeout)
    except Exception:  # noqa: BLE001 - we are leaving; nothing to do about it
        return False


def publish_status(client, *, bot_instance_id, owner_id) -> bool:
    """Upsert this bot's learning_writer_status row. False when there is
    nothing to publish or it could not be published; never raises."""
    from app.learning import provenance, status

    writer, recorder = _state["writer"], _state["recorder"]
    if writer is None or client is None or not bot_instance_id:
        return False
    extra = {}
    if recorder is not None:
        stats = recorder.health()
        extra = {"decisions_opened": stats.get("decisions_opened", 0),
                 "decisions_deduped": stats.get("decisions_deduped", 0),
                 "rejections": stats.get("rejections", {})}
    return status.publish(client, writer, bot_instance_id=bot_instance_id, owner_id=owner_id,
                          adapter_version=provenance.ADAPTER_VERSION, extra=extra)


def health() -> dict:
    w, r = _state["writer"], _state["recorder"]
    if w is None and r is None:
        return {"enabled": False}
    report: dict = {"enabled": True}
    if w is not None:
        report.update(w.health())
    elif r is not None:
        report.update(r.outbox.stats())
        report["writer_alive"] = False
    if r is not None:
        report["recorder"] = r.health()
    return report
