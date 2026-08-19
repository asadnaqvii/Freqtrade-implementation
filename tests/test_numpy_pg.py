"""Tests for numpy -> psycopg2 adaptation.

The bug these cover took down every trade the bot tried to record, and reported
itself as `schema "np" does not exist` -- which points at search_path and has
nothing to do with it.
"""

from __future__ import annotations

import pytest

np = pytest.importorskip("numpy")
psycopg2 = pytest.importorskip("psycopg2")

from psycopg2.extensions import adapt  # noqa: E402

from app.core import numpy_pg  # noqa: E402


@pytest.fixture(autouse=True)
def _registered():
    numpy_pg._registered = False
    assert numpy_pg.register()
    yield


def quoted(value):
    return adapt(value).getquoted().decode()


def test_numpy_float_no_longer_serialises_as_a_repr():
    # Before: b'np.float64(1921.86)' -> Postgres reads np as a schema.
    assert quoted(np.float64(1921.86)) == "1921.86"
    assert "np." not in quoted(np.float64(1921.86))


@pytest.mark.parametrize("value", [
    np.float64(0.0), np.float64(-0.5), np.float64(1e-8), np.float64(64403.1),
    np.float32(2.5), np.float16(1.0),
])
def test_numpy_floats_round_trip_as_numbers(value):
    text = quoted(value)
    assert "np." not in text
    assert float(text) == pytest.approx(float(value))


@pytest.mark.parametrize("value", [
    np.int64(7), np.int32(-3), np.int16(0), np.int8(1),
    np.uint64(12), np.uint32(5), np.uint16(2), np.uint8(255),
])
def test_numpy_ints_round_trip_as_numbers(value):
    text = quoted(value)
    assert "np." not in text
    assert int(text) == int(value)


def test_numpy_bools_become_sql_booleans():
    assert quoted(np.bool_(True)) == "true"
    assert quoted(np.bool_(False)) == "false"


def test_plain_python_values_are_untouched():
    assert quoted(1921.86) == "1921.86"
    assert quoted(7) == "7"


def test_registering_twice_is_harmless():
    assert numpy_pg.register()
    assert numpy_pg.register()
    assert quoted(np.float64(1.5)) == "1.5"


def test_a_realistic_trade_row_contains_no_numpy_reprs():
    """The shape of the INSERT that was failing."""
    row = {
        "open_rate": np.float64(1921.86),
        "amount": np.float64(0.3836599),
        "stake_amount": np.float64(730.70329914),
        "is_open": np.bool_(True),
        "timeframe": np.int64(240),
        "close_rate": None,
    }
    rendered = {k: (quoted(v) if v is not None else "NULL") for k, v in row.items()}
    assert not any("np." in v for v in rendered.values()), rendered
    assert rendered["is_open"] == "true"
    assert rendered["timeframe"] == "240"
