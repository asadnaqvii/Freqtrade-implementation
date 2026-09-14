"""Tests for the strategy builder.

The important ones are the rejection tests. A generator that turns user input
into python is only safe if hostile input never reaches it, so these assert that
the spec layer refuses first.
"""

from __future__ import annotations

import pytest

from app.strategy_builder import codegen, compile_check
from app.strategy_builder import spec as S


def minimal(**overrides):
    payload = {
        "class_name": "Sample",
        "indicators": [{"id": "rsi14", "kind": "rsi", "params": {"period": 14}}],
        "entry": {"long": {"all": [{"left": "rsi14", "op": "lt", "right": {"const": 30}}]}},
    }
    payload.update(overrides)
    return payload


# ---------------------------------------------------------------------------
# Rejection: nothing hostile should reach the generator
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "class_name",
    [
        "lowercase",                     # must start uppercase
        "Has Space",
        "Semi;colon",
        'Quote"Break',
        "Sub)(class",
        "A",                             # too short
        "X" * 100,                       # too long
        "__import__",
    ],
)
def test_hostile_class_names_are_rejected(class_name):
    with pytest.raises(Exception):
        S.parse(minimal(class_name=class_name))


@pytest.mark.parametrize(
    "indicator_id",
    [
        "'; import os; x='",
        "rsi'] + __import__('os').system('id') + ['",
        "Upper",
        "has space",
        "trailing-dash",
        "",
    ],
)
def test_hostile_indicator_ids_are_rejected(indicator_id):
    payload = minimal()
    payload["indicators"] = [{"id": indicator_id, "kind": "rsi"}]
    with pytest.raises(Exception):
        S.parse(payload)


def test_unknown_indicator_kind_is_rejected():
    payload = minimal()
    payload["indicators"] = [{"id": "x", "kind": "os.system"}]
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "unknown indicator" in str(exc.value)


def test_rule_referencing_an_undeclared_column_is_rejected():
    payload = minimal()
    payload["entry"] = {
        "long": {"all": [{"left": "not_a_column", "op": "gt", "right": {"const": 1}}]}
    }
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "not a known column" in str(exc.value)


def test_injection_via_right_operand_is_rejected():
    payload = minimal()
    payload["entry"] = {
        "long": {"all": [{"left": "rsi14", "op": "gt", "right": "close'] ; import os #"}]}
    }
    with pytest.raises(Exception):
        S.parse(payload)


def test_unknown_parameter_is_rejected():
    payload = minimal()
    payload["indicators"] = [{"id": "r", "kind": "rsi", "params": {"periodd": 14}}]
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "no parameter" in str(exc.value)


def test_out_of_range_parameter_is_rejected():
    payload = minimal()
    payload["indicators"] = [{"id": "r", "kind": "rsi", "params": {"period": 100000}}]
    with pytest.raises(Exception):
        S.parse(payload)


def test_duplicate_indicator_ids_are_rejected():
    payload = minimal()
    payload["indicators"] = [
        {"id": "dup", "kind": "rsi"},
        {"id": "dup", "kind": "ema"},
    ]
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "duplicate indicator id" in str(exc.value)


def test_extra_top_level_keys_are_rejected():
    with pytest.raises(Exception):
        S.parse(minimal(surprise="payload"))


def test_short_entry_without_can_short_is_rejected():
    payload = minimal()
    payload["entry"] = {
        "long": {"all": [{"left": "rsi14", "op": "lt", "right": {"const": 30}}]},
        "short": {"all": [{"left": "rsi14", "op": "gt", "right": {"const": 70}}]},
    }
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "can_short" in str(exc.value)


def test_rule_group_with_two_operators_is_rejected():
    payload = minimal()
    payload["entry"] = {
        "long": {
            "all": [{"left": "rsi14", "op": "lt", "right": {"const": 30}}],
            "any": [{"left": "rsi14", "op": "gt", "right": {"const": 70}}],
        }
    }
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "exactly one" in str(exc.value)


def test_trailing_offset_below_positive_is_rejected():
    payload = minimal()
    payload["risk"] = {
        "stoploss": -0.05,
        "minimal_roi": {"0": 0.05},
        "trailing": {"enabled": True, "positive": 0.05, "offset": 0.01},
    }
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "must exceed" in str(exc.value)


def test_positive_stoploss_is_rejected():
    with pytest.raises(Exception):
        S.parse(minimal(risk={"stoploss": 0.05, "minimal_roi": {"0": 0.05}}))


def test_roi_without_zero_key_is_rejected():
    with pytest.raises(Exception) as exc:
        S.parse(minimal(risk={"stoploss": -0.05, "minimal_roi": {"60": 0.02}}))
    assert '"0"' in str(exc.value)


def test_between_requires_ordered_bounds():
    payload = minimal()
    payload["entry"] = {
        "long": {"all": [{"left": "rsi14", "op": "between", "right": [70, 30]}]}
    }
    with pytest.raises(Exception) as exc:
        S.parse(payload)
    assert "low < high" in str(exc.value)


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------

def test_startup_candles_track_the_slowest_indicator():
    fast = S.parse(minimal(indicators=[{"id": "e", "kind": "ema", "params": {"period": 9}}],
                           entry={"long": {"all": [{"left": "e", "op": "gt", "right": "close"}]}}))
    slow = S.parse(minimal(indicators=[{"id": "e", "kind": "ema", "params": {"period": 200}}],
                           entry={"long": {"all": [{"left": "e", "op": "gt", "right": "close"}]}}))
    assert slow.required_startup_candles() > fast.required_startup_candles()


def test_entries_are_volume_guarded():
    source = codegen.generate(S.parse(minimal()))
    assert "dataframe['volume'] > 0" in source


def test_not_group_negates_the_union():
    payload = minimal()
    payload["entry"] = {
        "long": {"not": [{"left": "rsi14", "op": "gt", "right": {"const": 70}}]}
    }
    source = codegen.generate(S.parse(payload))
    assert "~(" in source


def test_between_renders_as_a_bounded_test():
    payload = minimal()
    payload["entry"] = {
        "long": {"all": [{"left": "rsi14", "op": "between", "right": [30, 70]}]}
    }
    source = codegen.generate(S.parse(payload))
    assert ".between(30.0, 70.0)" in source


def test_spec_hash_is_stable_and_content_addressed():
    a = S.parse(minimal())
    b = S.parse(minimal())
    assert codegen.spec_hash(a) == codegen.spec_hash(b)
    c = S.parse(minimal(timeframe="1h"))
    assert codegen.spec_hash(a) != codegen.spec_hash(c)


# ---------------------------------------------------------------------------
# The real thing: generated strategies must import and run
# ---------------------------------------------------------------------------

FULL_SPEC = {
    "class_name": "FullFeature",
    "description": "Exercises every operator and indicator in the catalog",
    "timeframe": "5m",
    "indicators": [
        {"id": "rsi14", "kind": "rsi"},
        {"id": "ema_fast", "kind": "ema", "params": {"period": 9}},
        {"id": "ema_slow", "kind": "ema", "params": {"period": 50}},
        {"id": "sma50", "kind": "sma"},
        {"id": "macd", "kind": "macd"},
        {"id": "bb", "kind": "bbands"},
        {"id": "atr", "kind": "atr"},
        {"id": "adx", "kind": "adx"},
        {"id": "st", "kind": "stoch"},
        {"id": "cci", "kind": "cci"},
        {"id": "mfi", "kind": "mfi"},
        {"id": "vol", "kind": "volume_mean"},
    ],
    "entry": {
        "long": {
            "all": [
                {"left": "ema_fast", "op": "crosses_above", "right": "ema_slow"},
                {"left": "rsi14", "op": "between", "right": [30, 70]},
                {"left": "adx", "op": "gte", "right": {"const": 20}},
                {"left": "close", "op": "gt", "right": "bb_lower"},
                {"left": "volume", "op": "gt", "right": "vol"},
                {"not": [{"left": "cci", "op": "gt", "right": {"const": 200}}]},
                {"any": [
                    {"left": "macd", "op": "gt", "right": "macd_signal"},
                    {"left": "st_k", "op": "crosses_above", "right": "st_d"},
                ]},
            ]
        }
    },
    "exit": {
        "long": {
            "any": [
                {"left": "rsi14", "op": "gt", "right": {"const": 75}},
                {"left": "ema_fast", "op": "crosses_below", "right": "ema_slow"},
                {"left": "close", "op": "gte", "right": "bb_upper"},
                {"left": "mfi", "op": "gt", "right": {"const": 80}},
                {"left": "sma50", "op": "lt", "right": "atr"},
            ]
        }
    },
    "risk": {
        "stoploss": -0.06,
        "minimal_roi": {"0": 0.08, "60": 0.03, "240": 0.01},
        "trailing": {"enabled": True, "positive": 0.015, "offset": 0.03},
    },
}


def test_full_spec_compiles_and_runs():
    parsed = S.parse(FULL_SPEC)
    source = codegen.generate(parsed)
    result = compile_check.check(source, parsed.class_name)
    assert result.ok, result.error
    # Every declared indicator must have produced its columns.
    for indicator in parsed.indicators:
        for column in indicator.columns():
            assert column in result.columns, f"{column} missing from the dataframe"


def test_generated_source_is_deterministic():
    parsed = S.parse(FULL_SPEC)
    assert codegen.generate(parsed) == codegen.generate(parsed)


def test_short_strategy_compiles():
    payload = {
        "class_name": "ShortSeller",
        "can_short": True,
        "indicators": [{"id": "rsi14", "kind": "rsi"}],
        "entry": {
            "long": {"all": [{"left": "rsi14", "op": "lt", "right": {"const": 30}}]},
            "short": {"all": [{"left": "rsi14", "op": "gt", "right": {"const": 70}}]},
        },
        "exit": {
            "long": {"all": [{"left": "rsi14", "op": "gt", "right": {"const": 60}}]},
            "short": {"all": [{"left": "rsi14", "op": "lt", "right": {"const": 40}}]},
        },
    }
    parsed = S.parse(payload)
    result = compile_check.check(codegen.generate(parsed), parsed.class_name)
    assert result.ok, result.error


def test_strategy_with_no_exit_rules_still_compiles():
    parsed = S.parse(minimal())
    result = compile_check.check(codegen.generate(parsed), parsed.class_name)
    assert result.ok, result.error
