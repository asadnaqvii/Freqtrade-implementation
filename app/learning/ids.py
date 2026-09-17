"""Time-ordered identifiers.

Python 3.11 has no uuid7(); this is RFC 9562 section 5.7. The point of a v7
id over a v4 is that sorting by id sorts by time, so an index on decision_id
is also an index on when the decision was made -- and a counter in the
rand_a bits keeps two ids minted in the same millisecond in the order they
were minted.
"""

from __future__ import annotations

import secrets
import threading
import time
import uuid

_lock = threading.Lock()
_last_ms = 0
_counter = 0


def uuid7(now_ms: int | None = None) -> uuid.UUID:
    global _last_ms, _counter
    with _lock:
        ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        if ms <= _last_ms:
            ms = _last_ms
            _counter += 1
            if _counter > 0xFFF:            # 12 bits of rand_a used up: borrow a millisecond
                ms += 1
                _last_ms = ms
                _counter = 0
        else:
            _last_ms = ms
            _counter = 0
        rand_a = _counter
        rand_b = secrets.randbits(62)
    value = ((ms & ((1 << 48) - 1)) << 80) | (0x7 << 76) | (rand_a << 64) | (0b10 << 62) | rand_b
    return uuid.UUID(int=value)


def new_decision_id() -> str:
    return str(uuid7())


def new_event_id() -> str:
    return str(uuid7())


def new_position_id() -> str:
    return str(uuid7())
