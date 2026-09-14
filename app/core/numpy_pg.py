"""Teach psycopg2 how to send numpy scalars to Postgres.

freqtrade computes with pandas and numpy, so the values it writes back --
prices, amounts, profits -- are frequently numpy scalars rather than Python
floats and ints. psycopg2 has no adapter for them.

For numpy 1.x that was harmless: np.float64 subclasses Python float, so
psycopg2's float adapter picked it up and called repr(), which returned
"1921.86". numpy 2.0 changed that repr to "np.float64(1921.86)", and psycopg2
inlines it verbatim. Postgres then reads `np.float64` as schema.function and
rejects the statement with:

    InvalidSchemaName: schema "np" does not exist

The error names a schema, so it reads like a search_path problem and sends you
looking in entirely the wrong place. It is really a serialisation bug, and it
breaks every INSERT the bot attempts.

Registering explicit adapters converts each numpy scalar to its native Python
equivalent before psycopg2 ever sees it.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

_registered = False


def register() -> bool:
    """Register numpy adapters with psycopg2. Idempotent; safe if either is absent."""
    global _registered
    if _registered:
        return True

    try:
        import numpy as np
        from psycopg2.extensions import AsIs, register_adapter
    except ImportError as exc:
        log.debug("numpy/psycopg2 adapters not registered: %s", exc)
        return False

    def as_float(value):
        return AsIs(repr(float(value)))

    def as_int(value):
        return AsIs(repr(int(value)))

    def as_bool(value):
        return AsIs("true" if bool(value) else "false")

    mappings = [
        (np.float64, as_float), (np.float32, as_float), (np.float16, as_float),
        (np.int64, as_int), (np.int32, as_int), (np.int16, as_int), (np.int8, as_int),
        (np.uint64, as_int), (np.uint32, as_int), (np.uint16, as_int), (np.uint8, as_int),
        (np.bool_, as_bool),
    ]
    # numpy 2 renamed a few aliases; skip anything this build does not have.
    for name, fn in (("longdouble", as_float), ("float128", as_float)):
        kind = getattr(np, name, None)
        if kind is not None:
            mappings.append((kind, fn))

    for kind, adapter in mappings:
        register_adapter(kind, adapter)

    _registered = True
    log.info("registered %d numpy adapters for psycopg2", len(mappings))
    return True
