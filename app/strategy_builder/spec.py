"""The strategy spec -- "the predefined format".

A strategy is authored as JSON matching these models, never as python. The rule
tree is a closed algebra: boolean groups over comparisons, whose operands are
either a column produced by a catalogued indicator, a raw OHLCV column, or a
number. Nothing a user types ends up in executable position.

Validation happens here, before the generator ever runs, so `codegen` can assume
it has been handed something coherent: every referenced column exists, every
indicator id is unique, every parameter is in range.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.strategy_builder import catalog

SPEC_VERSION = "1.0"

CLASS_NAME_RE = re.compile(r"^[A-Z][A-Za-z0-9_]{2,63}$")
INDICATOR_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# freqtrade's supported candle sizes. Constrained because the value is passed
# straight to the CLI and written into the generated class.
TIMEFRAMES: tuple[str, ...] = (
    "1m", "3m", "5m", "15m", "30m",
    "1h", "2h", "4h", "6h", "8h", "12h",
    "1d", "3d", "1w",
)

COMPARISONS: tuple[str, ...] = (
    "gt", "gte", "lt", "lte", "crosses_above", "crosses_below", "between",
)

_OPERATOR_SYMBOL = {"gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


class Constant(BaseModel):
    """A literal number on the right-hand side of a comparison."""

    model_config = ConfigDict(extra="forbid")
    const: float


class Indicator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="Unique handle used to reference this indicator's output.")
    kind: str = Field(description="Catalog key, e.g. 'rsi'.")
    params: dict[str, Any] = Field(default_factory=dict)

    @field_validator("id")
    @classmethod
    def _valid_id(cls, v: str) -> str:
        if not INDICATOR_ID_RE.match(v):
            raise ValueError(
                f"indicator id {v!r} must be lowercase alphanumeric with underscores, "
                "starting with a letter"
            )
        return v

    @field_validator("kind")
    @classmethod
    def _known_kind(cls, v: str) -> str:
        catalog.get(v)  # raises ValueError naming the valid options
        return v

    @model_validator(mode="after")
    def _valid_params(self) -> "Indicator":
        definition = catalog.get(self.kind)
        # Normalising here means downstream code sees defaults filled in.
        object.__setattr__(self, "params", definition.resolve_params(self.params))
        return self

    @property
    def definition(self) -> catalog.IndicatorDef:
        return catalog.get(self.kind)

    def columns(self) -> list[str]:
        return self.definition.columns(self.id, self.params)

    def startup_candles(self) -> int:
        return self.definition.startup_candles(self.params)


class Comparison(BaseModel):
    """One leaf test, e.g. `rsi14 < 30` or `ema9 crosses_above ema21`."""

    model_config = ConfigDict(extra="forbid")

    left: str
    op: Literal["gt", "gte", "lt", "lte", "crosses_above", "crosses_below", "between"]
    right: Union[str, Constant, list[float], None] = None

    @model_validator(mode="after")
    def _shape(self) -> "Comparison":
        if self.op == "between":
            if not isinstance(self.right, list) or len(self.right) != 2:
                raise ValueError("'between' needs right to be a two-element [low, high]")
            low, high = self.right
            if low >= high:
                raise ValueError(f"'between' needs low < high, got [{low}, {high}]")
        else:
            if self.right is None:
                raise ValueError(f"operator {self.op!r} needs a right-hand operand")
            if isinstance(self.right, list):
                raise ValueError(f"operator {self.op!r} does not take a list")
        return self


class RuleGroup(BaseModel):
    """Boolean combination. Exactly one of all/any/none is set."""

    model_config = ConfigDict(extra="forbid")

    all: list["Rule"] | None = None
    any: list["Rule"] | None = None
    # `not` is a python keyword; the JSON key stays "not" via the alias.
    none: list["Rule"] | None = Field(default=None, alias="not")

    @model_validator(mode="after")
    def _exactly_one(self) -> "RuleGroup":
        present = [name for name in ("all", "any", "none") if getattr(self, name)]
        if len(present) != 1:
            raise ValueError(
                "a rule group must set exactly one of 'all', 'any' or 'not'; "
                f"got {present or 'none of them'}"
            )
        return self

    def children(self) -> list["Rule"]:
        return self.all or self.any or self.none or []


Rule = Annotated[Union[RuleGroup, Comparison], Field(union_mode="left_to_right")]
RuleGroup.model_rebuild()


class Signals(BaseModel):
    """Entry or exit conditions, per direction."""

    model_config = ConfigDict(extra="forbid")

    long: Rule | None = None
    short: Rule | None = None

    @model_validator(mode="after")
    def _at_least_one(self) -> "Signals":
        if self.long is None and self.short is None:
            raise ValueError("define at least one of 'long' or 'short'")
        return self


class Trailing(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    positive: float = Field(default=0.01, gt=0, lt=1)
    offset: float = Field(default=0.02, gt=0, lt=1)
    only_offset_reached: bool = True

    @model_validator(mode="after")
    def _offset_above_positive(self) -> "Trailing":
        # freqtrade rejects a config where the trail starts below the trigger,
        # and the failure at startup is opaque. Catch it here instead.
        if self.enabled and self.offset <= self.positive:
            raise ValueError(
                f"trailing offset ({self.offset}) must exceed positive ({self.positive}); "
                "otherwise the stop would trail from below its own trigger"
            )
        return self


class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stoploss: float = Field(default=-0.05, gt=-1.0, lt=0)
    minimal_roi: dict[str, float] = Field(default_factory=lambda: {"0": 0.05})
    trailing: Trailing = Field(default_factory=Trailing)

    @field_validator("minimal_roi")
    @classmethod
    def _roi_shape(cls, v: dict[str, float]) -> dict[str, float]:
        if not v:
            raise ValueError("minimal_roi needs at least one entry, e.g. {\"0\": 0.05}")
        for key, value in v.items():
            if not re.match(r"^\d+$", str(key)):
                raise ValueError(
                    f"minimal_roi keys are minutes-since-entry as digits, got {key!r}"
                )
            if value < 0:
                raise ValueError(f"minimal_roi[{key}] must be >= 0, got {value}")
        if "0" not in v:
            raise ValueError("minimal_roi must include a \"0\" entry (the target at entry)")
        return v


class StrategySpec(BaseModel):
    """A complete, self-contained strategy definition."""

    model_config = ConfigDict(extra="forbid")

    spec_version: Literal["1.0"] = SPEC_VERSION
    class_name: str
    description: str | None = None
    timeframe: str = "5m"
    can_short: bool = False
    indicators: list[Indicator] = Field(default_factory=list)
    entry: Signals
    exit: Signals | None = None
    risk: Risk = Field(default_factory=Risk)
    startup_candle_count: int | None = None
    process_only_new_candles: bool = True
    use_exit_signal: bool = True

    @field_validator("class_name")
    @classmethod
    def _valid_class(cls, v: str) -> str:
        if not CLASS_NAME_RE.match(v):
            raise ValueError(
                f"class_name {v!r} must start uppercase and contain only letters, "
                "digits and underscores (3-64 chars)"
            )
        return v

    @field_validator("timeframe")
    @classmethod
    def _valid_timeframe(cls, v: str) -> str:
        if v not in TIMEFRAMES:
            raise ValueError(f"timeframe must be one of {', '.join(TIMEFRAMES)}, got {v!r}")
        return v

    # -- cross-field checks ------------------------------------------------
    @model_validator(mode="after")
    def _coherent(self) -> "StrategySpec":
        ids = [i.id for i in self.indicators]
        duplicates = {i for i in ids if ids.count(i) > 1}
        if duplicates:
            raise ValueError(f"duplicate indicator id(s): {', '.join(sorted(duplicates))}")

        known = self.available_columns()

        for label, signals in (("entry", self.entry), ("exit", self.exit)):
            if signals is None:
                continue
            for direction in ("long", "short"):
                rule = getattr(signals, direction)
                if rule is None:
                    continue
                if direction == "short" and not self.can_short:
                    raise ValueError(
                        f"{label}.short is defined but can_short is false; "
                        "spot accounts cannot short"
                    )
                _check_columns(rule, known, f"{label}.{direction}")

        if self.can_short and self.entry.short is None:
            raise ValueError("can_short is true but no entry.short rule is defined")

        return self

    # -- derived -----------------------------------------------------------
    def available_columns(self) -> set[str]:
        """Every column a rule may legally reference."""
        columns: set[str] = set(catalog.OHLCV_COLUMNS)
        for indicator in self.indicators:
            columns.update(indicator.columns())
        return columns

    def required_startup_candles(self) -> int:
        """Warm-up the generated strategy will declare.

        Taking the max over indicators means the strategy asks for exactly the
        history its slowest component needs, rather than a guessed constant.
        """
        if self.startup_candle_count is not None:
            return self.startup_candle_count
        if not self.indicators:
            return 30
        return max(i.startup_candles() for i in self.indicators)


def _check_columns(rule: Any, known: set[str], where: str) -> None:
    """Walk a rule tree and reject any operand that is not a real column."""
    if isinstance(rule, RuleGroup):
        for child in rule.children():
            _check_columns(child, known, where)
        return

    if isinstance(rule, Comparison):
        if rule.left not in known:
            raise ValueError(
                f"{where}: {rule.left!r} is not a known column. "
                f"Available: {', '.join(sorted(known))}"
            )
        if isinstance(rule.right, str) and rule.right not in known:
            raise ValueError(
                f"{where}: {rule.right!r} is not a known column. "
                f"Available: {', '.join(sorted(known))}"
            )
        if rule.op in ("crosses_above", "crosses_below") and isinstance(rule.right, Constant):
            # Crossing a constant is legitimate but people usually mean a column;
            # allow it, since qtpylib handles a scalar fine.
            pass
        return

    raise ValueError(f"{where}: unrecognised rule node {type(rule).__name__}")


def parse(payload: dict[str, Any]) -> StrategySpec:
    """Validate a raw dict into a StrategySpec.

    Kept as a function so callers get one obvious entry point and pydantic stays
    an implementation detail.
    """
    return StrategySpec.model_validate(payload)
