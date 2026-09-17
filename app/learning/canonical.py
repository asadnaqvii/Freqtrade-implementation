"""One way to write a record down, so the same record hashes the same way.

What comes out of freqtrade is a pandas row: numpy scalars, Timestamps, NaN
where an indicator has not warmed up. None of that is JSON. `sanitise` turns
it into plain Python the same way every time; `canonical_json` writes it with
sorted keys and no whitespace; `payload_sha256` hashes everything except the
fields that change on replay, so a record captured twice proves it is the
same record.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

#: Fields that legitimately differ between two captures of the same record.
VOLATILE_FIELDS = frozenset({"recorded_at_utc", "payload_sha256", "event_id"})

_MAX_REPR = 200


def sanitise(value: Any, _depth: int = 0) -> Any:
    """Plain Python, JSON-safe, deterministic. Never raises."""
    if _depth > 32:
        return {"_unserialisable": "nesting too deep"}
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, Enum):
        return sanitise(value.value, _depth + 1)
    if isinstance(value, float):
        # numpy.float64 subclasses float; the cast makes it a plain one.
        return None if (math.isnan(value) or math.isinf(value)) else float(value)
    if isinstance(value, Decimal):
        return None if value.is_nan() else float(value)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"_bytes": value[:64].hex(), "_len": len(value)}
    if isinstance(value, dict):
        return {str(k): sanitise(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [sanitise(v, _depth + 1) for v in value]

    module = type(value).__module__ or ""
    # numpy scalars and pandas Timestamps, without importing either.
    if module.startswith("numpy") and hasattr(value, "item"):
        try:
            return sanitise(value.item(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "isoformat") and hasattr(value, "tzinfo"):
        try:
            if value.tzinfo is None:
                value = value.tz_localize("UTC") if hasattr(value, "tz_localize") else value
            return sanitise(value.to_pydatetime() if hasattr(value, "to_pydatetime") else value,
                            _depth + 1)
        except Exception:  # noqa: BLE001
            pass
    if hasattr(value, "tolist"):
        try:
            return sanitise(value.tolist(), _depth + 1)
        except Exception:  # noqa: BLE001
            pass
    return {"_unserialisable": repr(value)[:_MAX_REPR]}


def canonical_json(value: Any) -> str:
    return json.dumps(sanitise(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def payload_sha256(record: dict) -> str:
    stable = {k: v for k, v in record.items() if k not in VOLATILE_FIELDS}
    return hashlib.sha256(canonical_json(stable).encode("utf-8")).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
