"""The Learning Module's contracts: ids, keys, canonical form, records.

What these pin down is the property the whole module rests on: a record
captured twice is the same record -- same key, same hash -- and a record
never contains data from after the moment it describes. freqtrade hands the
adapter pandas rows, so the numpy and NaN cases are the ones that matter.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import numpy as np
import pandas as pd
import pytest

from app.learning import canonical, ids, keys, provenance
from app.learning.contracts import SCHEMA_VERSION, LeakageError, TradingDecision, TradingEvent
from app.learning.enums import REJECTION_MEANING, DecisionKind, EventType, RejectionCode


def a_future_millisecond(offset_ms: int) -> int:
    """A millisecond past anything the id generator has seen, so tests do not
    depend on the order they run in."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    return max(now_ms, ids._last_ms) + offset_ms


# -- ids -------------------------------------------------------------------
def test_ids_are_version_7_uuids():
    value = ids.uuid7()
    assert value.version == 7
    assert value.variant == uuid.RFC_4122


def test_ids_sort_by_the_time_they_were_minted():
    base = a_future_millisecond(60_000)
    earlier = ids.uuid7(base)
    later = ids.uuid7(base + 1_000)
    assert earlier < later
    assert str(earlier) < str(later)


def test_ids_minted_in_the_same_millisecond_keep_their_order():
    base = a_future_millisecond(60_000)
    minted = [ids.uuid7(base) for _ in range(50)]
    assert minted == sorted(minted)
    assert len(set(minted)) == 50


def test_ids_never_go_backwards_when_the_clock_does():
    base = a_future_millisecond(60_000)
    first = ids.uuid7(base)
    second = ids.uuid7(base - 5_000)
    assert second > first


def test_the_id_helpers_hand_out_strings_of_version_7():
    for make in (ids.new_decision_id, ids.new_event_id, ids.new_position_id):
        value = make()
        assert isinstance(value, str)
        assert uuid.UUID(value).version == 7


# -- canonical form --------------------------------------------------------
def test_nan_and_infinity_become_null():
    assert canonical.sanitise(float("nan")) is None
    assert canonical.sanitise(float("inf")) is None
    assert canonical.sanitise(np.float64("nan")) is None
    # The first hundred bars of a new pair look exactly like this.
    assert canonical.sanitise({"adx_falling": np.nan, "atr_pct_median": np.nan}) == {
        "adx_falling": None, "atr_pct_median": None,
    }


def test_numpy_scalars_become_plain_python():
    assert canonical.sanitise(np.bool_(True)) is True
    assert canonical.sanitise(np.bool_(False)) is False
    seven = canonical.sanitise(np.int64(7))
    assert seven == 7 and type(seven) is int
    ratio = canonical.sanitise(np.float64(1.5))
    assert ratio == 1.5 and type(ratio) is float
    assert canonical.sanitise(np.array([1, 2])) == [1, 2]


def test_timestamps_become_iso_8601_in_utc():
    assert canonical.sanitise(pd.Timestamp("2026-09-17 04:00", tz="UTC")) == "2026-09-17T04:00:00+00:00"
    assert canonical.sanitise(pd.Timestamp("2026-09-17 04:00")) == "2026-09-17T04:00:00+00:00"
    assert canonical.sanitise(datetime(2026, 9, 17, 4, 0)) == "2026-09-17T04:00:00+00:00"
    shifted = datetime(2026, 9, 17, 6, 0, tzinfo=timezone(timedelta(hours=2)))
    assert canonical.sanitise(shifted) == "2026-09-17T04:00:00+00:00"


def test_enums_decimals_bytes_and_sets_are_written_plainly():
    assert canonical.sanitise(DecisionKind.ENTRY) == "entry"
    assert canonical.sanitise(EventType.FILL_COMPLETED) == "fill_completed"
    assert canonical.sanitise(Decimal("1.25")) == 1.25
    assert canonical.sanitise(b"\x00\x01") == {"_bytes": "0001", "_len": 2}
    assert sorted(canonical.sanitise({3, 1, 2})) == [1, 2, 3]
    assert canonical.sanitise((1, "a")) == [1, "a"]


def test_an_object_that_cannot_be_written_is_marked_rather_than_raised():
    class Odd:
        def __repr__(self) -> str:
            return "Odd()"

    assert canonical.sanitise(Odd()) == {"_unserialisable": "Odd()"}
    assert canonical.sanitise({"inner": [Odd()]}) == {"inner": [{"_unserialisable": "Odd()"}]}


def test_canonical_json_is_sorted_and_compact():
    assert canonical.canonical_json({"b": 1, "a": [1, {"d": 2, "c": 3}]}) == '{"a":[1,{"c":3,"d":2}],"b":1}'


def test_the_hash_ignores_the_fields_that_change_on_replay():
    first = {
        "symbol": "BTC/USDT",
        "recorded_at_utc": "2026-09-17T04:00:00+00:00",
        "event_id": "a",
        "payload_sha256": "x",
    }
    replayed = {**first, "recorded_at_utc": "2026-09-17T04:00:05+00:00",
                "event_id": "b", "payload_sha256": "y"}
    assert canonical.payload_sha256(first) == canonical.payload_sha256(replayed)
    assert canonical.payload_sha256({**first, "symbol": "ETH/USDT"}) != canonical.payload_sha256(first)


def test_the_hash_does_not_depend_on_key_order():
    assert canonical.payload_sha256({"a": 1, "b": 2}) == canonical.payload_sha256({"b": 2, "a": 1})


# -- keys ------------------------------------------------------------------
DECISION = dict(
    bot_instance_id="bot-1",
    strategy_id="TrendPullbackStrategy_v3",
    strategy_code_hash="abc123",
    symbol="TRX/USDT",
    timeframe="4h",
    decision_kind="entry",
    strategy_intent="enter_long",
    candle_open="2026-09-17T04:00:00+00:00",
)


def test_the_same_decision_has_the_same_key_however_often_it_is_seen():
    # freqtrade re-evaluates the same candle every five seconds.
    assert keys.decision_key(**DECISION) == keys.decision_key(**DECISION)


def test_the_next_candle_is_a_new_decision():
    following = {**DECISION, "candle_open": "2026-09-17T08:00:00+00:00"}
    assert keys.decision_key(**following) != keys.decision_key(**DECISION)


def test_a_changed_strategy_file_is_a_new_decision():
    edited = {**DECISION, "strategy_code_hash": "def456"}
    assert keys.decision_key(**edited) != keys.decision_key(**DECISION)


def test_a_second_decision_on_the_same_candle_is_told_apart_by_seq():
    assert keys.decision_key(**DECISION, seq=1) != keys.decision_key(**DECISION)


def test_events_in_the_same_second_share_a_key_and_a_new_order_does_not():
    at = datetime(2026, 9, 17, 4, 0, 0, 250_000, tzinfo=timezone.utc)
    later = at.replace(microsecond=900_000)
    first = keys.event_key(decision_id="d", event_type="order_submitted", event_time=at, order_ref="o1")
    again = keys.event_key(decision_id="d", event_type="order_submitted", event_time=later, order_ref="o1")
    assert first == again
    other_order = keys.event_key(decision_id="d", event_type="order_submitted", event_time=at, order_ref="o2")
    assert other_order != first
    other_type = keys.event_key(decision_id="d", event_type="order_acknowledged", event_time=at, order_ref="o1")
    assert other_type != first


def test_time_buckets_floor_to_the_bucket_and_accept_iso_strings():
    assert keys.time_bucket("2026-09-17T04:00:59Z", 60) == "2026-09-17T04:00:00+00:00"
    assert keys.time_bucket(datetime(2026, 9, 17, 4, 0, 59, 999_000), 1) == "2026-09-17T04:00:59+00:00"


# -- records ---------------------------------------------------------------
def decision(**overrides) -> TradingDecision:
    fields = dict(
        decision_id=ids.new_decision_id(),
        decision_time_utc="2026-09-17T04:00:05+00:00",
        environment="staging",
        exchange="kucoin",
        symbol="TRX/USDT",
        timeframe="4h",
        decision_kind="entry",
        strategy_intent="enter_long",
        strategy_id="TrendPullbackStrategy_v3",
        feature_set_version="tp-v3.1",
        idempotency_key="k",
        market_data_max_ts="2026-09-17T00:00:00+00:00",
        feature_snapshot={"rsi": np.float64(61.2), "adx_falling": np.nan, "ema_ok": np.bool_(True)},
    )
    fields.update(overrides)
    return TradingDecision(**fields)


def test_a_decision_is_frozen():
    record = decision()
    with pytest.raises(dataclasses.FrozenInstanceError):
        record.symbol = "ETH/USDT"  # type: ignore[misc]


def test_a_snapshot_from_after_the_decision_is_leakage():
    with pytest.raises(LeakageError):
        decision(market_data_max_ts="2026-09-17T04:00:06+00:00").validate()


def test_a_candle_that_closed_before_the_decision_is_fine():
    decision().validate()
    decision(market_data_max_ts="2026-09-17T04:00:05+00:00").validate()
    decision(market_data_max_ts=None).validate()


def test_a_decision_missing_its_identity_is_refused():
    with pytest.raises(ValueError, match="symbol is required"):
        decision(symbol="").validate()


def test_the_row_is_plain_json_with_its_hash():
    row = decision().to_row()
    json.dumps(row, allow_nan=False)
    assert row["feature_snapshot"] == {"rsi": 61.2, "adx_falling": None, "ema_ok": True}
    assert row["schema_version"] == SCHEMA_VERSION == 1
    assert row["payload_sha256"] == canonical.payload_sha256(row)


def test_two_captures_of_one_decision_hash_the_same():
    first = decision(decision_id="d-1").to_row()
    second = decision(decision_id="d-1").to_row()
    assert first["payload_sha256"] == second["payload_sha256"]


def test_an_event_hash_survives_a_new_event_id():
    event = TradingEvent(
        event_id=ids.new_event_id(),
        event_type="order_submitted",
        event_time_utc="2026-09-17T04:00:06+00:00",
        event_source="freqtrade_callback",
        idempotency_key="e",
        decision_id="d-1",
        payload={"amount": np.float64(12)},
    )
    again = dataclasses.replace(event, event_id=ids.new_event_id())
    assert event.to_row()["payload_sha256"] == again.to_row()["payload_sha256"]
    assert event.to_row()["payload"] == {"amount": 12.0}
    event.validate()


def test_an_event_without_a_source_is_refused():
    with pytest.raises(ValueError, match="event_source is required"):
        TradingEvent(event_id="e", event_type="x", event_time_utc="t", event_source="",
                     idempotency_key="k").validate()


# -- vocabulary ------------------------------------------------------------
def test_every_rejection_code_is_explained_in_plain_words():
    for code in RejectionCode:
        assert code.value in REJECTION_MEANING
        assert REJECTION_MEANING[code.value].endswith(".")


def test_enums_are_strings_on_the_wire():
    assert json.dumps({"kind": DecisionKind.ENTRY}) == '{"kind": "entry"}'


# -- provenance ------------------------------------------------------------
class Parameter:
    def __init__(self, value):
        self.value = value


class Strategy:
    INTERFACE_VERSION = 3

    def __init__(self, rsi):
        self._parameters = {"buy_rsi": Parameter(rsi)}

    def enumerate_parameters(self):
        return list(self._parameters.items())


def test_secrets_are_scrubbed_before_the_config_is_hashed():
    config = {
        "exchange": {"name": "kucoin", "key": "key-abc123", "secret": "secret-abc123", "password": "pass-abc123"},
        "api_server": {"username": "user-abc123", "password": "pw-abc123", "jwt_secret_key": "jwt-abc123"},
        "stake_amount": 10,
    }
    scrubbed = provenance.redacted_config(config)
    assert "abc123" not in json.dumps(scrubbed)
    assert scrubbed["exchange"]["name"] == "kucoin"
    assert config["exchange"]["key"] == "key-abc123"  # the caller's config is untouched


def test_two_configs_that_differ_only_in_secrets_hash_the_same():
    one = {"exchange": {"key": "a", "secret": "b"}, "stake_amount": 10}
    other = {"exchange": {"key": "c", "secret": "d"}, "stake_amount": 10}
    assert provenance.config_hash(one) == provenance.config_hash(other)
    assert provenance.config_hash({**one, "stake_amount": 20}) != provenance.config_hash(one)
    assert provenance.config_hash(None) is None


def test_the_parameter_hash_follows_the_values_the_strategy_runs_at():
    assert provenance.parameter_hash(Strategy(30)) == provenance.parameter_hash(Strategy(30))
    assert provenance.parameter_hash(Strategy(30)) != provenance.parameter_hash(Strategy(35))
    assert provenance.parameter_hash(object()) is None


def test_the_strategy_file_is_hashed_from_disk(tmp_path):
    (tmp_path / "S.py").write_text("x = 1\n")
    expected = hashlib.sha256(b"x = 1\n").hexdigest()
    assert provenance.strategy_sha("S", search_dirs=(str(tmp_path),)) == expected
    assert provenance.strategy_sha("Missing", search_dirs=(str(tmp_path),)) is None


def test_provenance_names_the_code_parameters_config_and_versions():
    built = provenance.build_provenance(
        strategy_name="S", strategy=Strategy(30), config={"exchange": {"key": "k"}},
        freqtrade_version="2026.7", environment="staging", extra={"trigger": "signal"},
    )
    assert built["feature_set_version"] == provenance.FEATURE_SET_VERSION
    assert built["adapter_version"] == provenance.ADAPTER_VERSION
    assert built["strategy_version"] == "3"
    assert built["parameter_hash"] and built["runtime_config_hash"]
    assert built["freqtrade_version"] == "2026.7"
    assert built["run_id"] is None
    assert built["trigger"] == "signal"


def test_a_deterministic_id_is_fixed_by_its_inputs_and_still_a_v7():
    when = int(datetime(2026, 9, 18, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    one = ids.deterministic_uuid7(when, "d1|bot|TRX/USDT")
    assert one == ids.deterministic_uuid7(when, "d1|bot|TRX/USDT")
    assert one != ids.deterministic_uuid7(when, "d1|bot|XRP/USDT")
    assert one.version == 7 and one.variant == uuid.RFC_4122
    later = ids.deterministic_uuid7(when + 4 * 3600 * 1000, "d1|bot|TRX/USDT")
    assert later > one and str(later) > str(one)
