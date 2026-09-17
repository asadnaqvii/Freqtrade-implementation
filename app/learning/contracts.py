"""The canonical records: a Trading Decision Record and the events that
hang off it. Frozen, because a decision snapshot is evidence and evidence
is not edited.

The dataclasses are typed for the code; `to_row()` is what goes to the
database, sanitised and hashed. `OutcomeLabel` and `HorizonProfile` are
defined so the vocabulary is fixed now, and are not persisted until the
outcome phases are built.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.learning.canonical import payload_sha256, sanitise

SCHEMA_VERSION = 1


class LeakageError(ValueError):
    """A decision snapshot contains data from after the decision was made."""


def _iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True, kw_only=True)
class TradingDecision:
    decision_id: str
    decision_time_utc: str
    environment: str
    exchange: str
    symbol: str
    timeframe: str
    decision_kind: str
    strategy_intent: str
    strategy_id: str
    feature_set_version: str
    idempotency_key: str
    market_type: str = "spot"
    owner_id: str | None = None
    bot_instance_id: str | None = None
    account_id: str | None = None
    run_id: str | None = None
    position_id: str | None = None
    strategy_version: str | None = None
    strategy_code_hash: str | None = None
    parameter_version: str | None = None
    parameter_hash: str | None = None
    runtime_config_hash: str | None = None
    model_id: str | None = None
    model_version: str | None = None
    entry_tag: str | None = None
    exit_reason: str | None = None
    market_data_max_ts: str | None = None
    market_context: dict = field(default_factory=dict)
    feature_snapshot: dict = field(default_factory=dict)
    portfolio_snapshot: dict = field(default_factory=dict)
    risk_snapshot: dict = field(default_factory=dict)
    prediction_snapshot: dict | None = None
    provenance: dict = field(default_factory=dict)
    quarantined: bool = False
    quarantine_reason: str | None = None
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        """Point-in-time correctness: nothing in the snapshot may postdate it."""
        for name in ("decision_id", "decision_time_utc", "exchange", "symbol", "timeframe",
                     "decision_kind", "strategy_intent", "strategy_id", "idempotency_key"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        if self.market_data_max_ts and _parse(self.market_data_max_ts) > _parse(self.decision_time_utc):
            raise LeakageError(
                f"market data from {self.market_data_max_ts} cannot inform a decision made at "
                f"{self.decision_time_utc}"
            )

    def to_row(self) -> dict[str, Any]:
        row = sanitise(asdict(self))
        row["payload_sha256"] = payload_sha256(row)
        return row


@dataclass(frozen=True, kw_only=True)
class TradingEvent:
    event_id: str
    event_type: str
    event_time_utc: str
    event_source: str
    idempotency_key: str
    decision_id: str | None = None
    owner_id: str | None = None
    bot_instance_id: str | None = None
    symbol: str | None = None
    position_id: str | None = None
    ft_trade_id: int | None = None
    ft_order_id: int | None = None
    exchange_order_id: str | None = None
    rejection_stage: str | None = None
    rejection_code: str | None = None
    payload: dict = field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def validate(self) -> None:
        for name in ("event_id", "event_type", "event_time_utc", "event_source", "idempotency_key"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")

    def to_row(self) -> dict[str, Any]:
        row = sanitise(asdict(self))
        row["payload_sha256"] = payload_sha256(row)
        return row


@dataclass(frozen=True, kw_only=True)
class HorizonProfile:
    """Per-strategy observation schedule. Defined now, persisted in phase 8."""
    profile_id: str
    strategy_id: str
    version: str
    bar_horizons: tuple[int, ...] = (1, 3, 6, 12)
    holding_multiples: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)
    calendar_horizons: tuple[str, ...] = ("1d", "3d", "1w")


@dataclass(frozen=True, kw_only=True)
class OutcomeLabel:
    """What the market did after a decision, at one horizon. Phase 8."""
    outcome_id: str
    decision_id: str
    label_version: str
    horizon: str
    reference_price: float | None = None
    future_price: float | None = None
    raw_return_bps: float | None = None
    side_adjusted_return_bps: float | None = None
    mfe_bps: float | None = None
    mae_bps: float | None = None
    data_quality: str = "MISSING"
    computed_at_utc: str | None = None


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
