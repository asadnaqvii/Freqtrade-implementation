"""Publish the writer's health where the dashboard can read it.

(Named status, not health: `app.learning.health()` is the package's accessor,
and a submodule of the same name would replace it on import.)

One row per bot in `learning_writer_status`, upserted from the bot's
heartbeat. The `v_learning_health` view joins it to what actually landed in
the evidence tables, so "the writer says it is fine" and "decisions are
arriving" are both visible, and a pipeline that died quietly is not.
"""

from __future__ import annotations

from datetime import datetime, timezone


def status_row(writer_health: dict, *, bot_instance_id: str, owner_id: str | None,
               adapter_version: str, extra: dict | None = None) -> dict:
    row = {
        "bot_instance_id": bot_instance_id,
        "owner_id": owner_id,
        "reported_at": datetime.now(timezone.utc).isoformat(),
        "enabled": True,
        "adapter_version": adapter_version,
        "outbox_pending": writer_health.get("outbox_pending") or 0,
        "outbox_oldest_age_seconds": writer_health.get("outbox_oldest_age_seconds"),
        "outbox_quarantined": writer_health.get("outbox_quarantined") or 0,
        "written_total": writer_health.get("written_total") or 0,
        "failed_total": writer_health.get("failed_total") or 0,
        "retried_total": writer_health.get("retried_total") or 0,
        "dropped_total": writer_health.get("dropped_total") or 0,
        "outages_total": writer_health.get("outages_total") or 0,
        "last_success_at": writer_health.get("last_success_at"),
        "last_error": writer_health.get("writer_error") or writer_health.get("last_error"),
    }
    if extra:
        row.update(extra)
    return row


def publish(client, writer, *, bot_instance_id: str, owner_id: str | None,
            adapter_version: str, extra: dict | None = None) -> bool:
    """Upsert the status row. False on any failure; never raises."""
    try:
        client.upsert("learning_writer_status",
                      status_row(writer.health(), bot_instance_id=bot_instance_id,
                                 owner_id=owner_id, adapter_version=adapter_version,
                                 extra=extra),
                      on_conflict="bot_instance_id", returning=False)
        return True
    except Exception:  # noqa: BLE001 - a status that cannot be published is not worth a crash
        return False
