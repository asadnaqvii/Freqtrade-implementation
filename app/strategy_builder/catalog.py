"""The closed vocabulary the strategy builder can emit.

Every indicator a user can pick is defined here, and nowhere else. A spec that
names something outside this table is rejected before any code is generated,
which is what makes the generator safe: user input selects from a fixed set of
templates, it never becomes part of one.

The emitted code matches the idiom already used in strategies/ -- talib.abstract
as `ta`, indicator columns written onto `dataframe` -- so a generated strategy
reads like a hand-written one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

# Column names must be safe to embed in generated python and in a dataframe key.
COLUMN_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# The raw candle columns freqtrade always provides.
OHLCV_COLUMNS: tuple[str, ...] = ("open", "high", "low", "close", "volume")

PRICE_SOURCES: tuple[str, ...] = ("open", "high", "low", "close")


@dataclass(frozen=True)
class ParamDef:
    name: str
    kind: type
    default: Any
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[str, ...] | None = None

    def coerce(self, value: Any) -> Any:
        """Validate and normalise one parameter value.

        Raises ValueError with a message meant for an end user, since these
        surface directly in the builder UI.
        """
        if value is None:
            value = self.default

        if self.choices is not None:
            text = str(value)
            if text not in self.choices:
                raise ValueError(
                    f"{self.name} must be one of {', '.join(self.choices)}, got {text!r}"
                )
            return text

        try:
            coerced = self.kind(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{self.name} must be {self.kind.__name__}, got {value!r}") from exc

        if self.minimum is not None and coerced < self.minimum:
            raise ValueError(f"{self.name} must be >= {self.minimum}, got {coerced}")
        if self.maximum is not None and coerced > self.maximum:
            raise ValueError(f"{self.name} must be <= {self.maximum}, got {coerced}")
        return coerced


@dataclass(frozen=True)
class IndicatorDef:
    key: str
    label: str
    category: str
    description: str
    params: tuple[ParamDef, ...]
    # Column-name templates, formatted with the resolved params and the
    # indicator's own id, e.g. "{id}" -> "rsi_fast".
    outputs: tuple[str, ...]
    min_startup: int
    # Given (id, params) produce the lines that compute this indicator.
    emit: Callable[[str, dict[str, Any]], list[str]] = field(repr=False)

    def resolve_params(self, raw: dict[str, Any] | None) -> dict[str, Any]:
        raw = raw or {}
        unknown = set(raw) - {p.name for p in self.params}
        if unknown:
            raise ValueError(
                f"{self.key} has no parameter(s): {', '.join(sorted(unknown))}"
            )
        return {p.name: p.coerce(raw.get(p.name)) for p in self.params}

    def columns(self, indicator_id: str, params: dict[str, Any]) -> list[str]:
        cols = [t.format(id=indicator_id, **params) for t in self.outputs]
        for col in cols:
            if not COLUMN_RE.match(col):
                raise ValueError(f"{self.key} produced an unusable column name: {col!r}")
        return cols

    def startup_candles(self, params: dict[str, Any]) -> int:
        """How much history this indicator needs before its output is real.

        Scaled off the largest period-like parameter rather than a flat constant,
        so an EMA(200) asks for more warm-up than an EMA(9) instead of both
        claiming the catalog maximum.
        """
        periods = [
            v for k, v in params.items()
            if isinstance(v, (int, float)) and k in {"period", "slow", "fastk", "signal"}
        ]
        if not periods:
            return self.min_startup
        return max(self.min_startup, int(max(periods) * 2))


# ---------------------------------------------------------------------------
# Emitters
# ---------------------------------------------------------------------------
# Each returns the lines that go inside populate_indicators(). They assume
# `dataframe` and `ta` are in scope, which the generated module guarantees.


def _emit_rsi(iid: str, p: dict[str, Any]) -> list[str]:
    return [f"dataframe['{iid}'] = ta.RSI(dataframe, timeperiod={p['period']})"]


def _emit_ema(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"dataframe['{iid}'] = ta.EMA(dataframe, timeperiod={p['period']}, "
        f"price='{p['source']}')"
    ]


def _emit_sma(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"dataframe['{iid}'] = ta.SMA(dataframe, timeperiod={p['period']}, "
        f"price='{p['source']}')"
    ]


def _emit_macd(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"macd_{iid} = ta.MACD(dataframe, fastperiod={p['fast']}, "
        f"slowperiod={p['slow']}, signalperiod={p['signal']})",
        f"dataframe['{iid}'] = macd_{iid}['macd']",
        f"dataframe['{iid}_signal'] = macd_{iid}['macdsignal']",
        f"dataframe['{iid}_hist'] = macd_{iid}['macdhist']",
    ]


def _emit_bbands(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"bb_{iid} = ta.BBANDS(dataframe, timeperiod={p['period']}, "
        f"nbdevup={p['stddev']}, nbdevdn={p['stddev']})",
        f"dataframe['{iid}_lower'] = bb_{iid}['lowerband']",
        f"dataframe['{iid}_middle'] = bb_{iid}['middleband']",
        f"dataframe['{iid}_upper'] = bb_{iid}['upperband']",
        # Width is the useful derived series: it says how wide the channel is
        # relative to its own centre, so it is comparable across pairs.
        f"dataframe['{iid}_width'] = ("
        f"(dataframe['{iid}_upper'] - dataframe['{iid}_lower']) "
        f"/ dataframe['{iid}_middle'])",
    ]


def _emit_atr(iid: str, p: dict[str, Any]) -> list[str]:
    return [f"dataframe['{iid}'] = ta.ATR(dataframe, timeperiod={p['period']})"]


def _emit_adx(iid: str, p: dict[str, Any]) -> list[str]:
    return [f"dataframe['{iid}'] = ta.ADX(dataframe, timeperiod={p['period']})"]


def _emit_stoch(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"stoch_{iid} = ta.STOCH(dataframe, fastk_period={p['fastk']}, "
        f"slowk_period={p['slowk']}, slowd_period={p['slowd']})",
        f"dataframe['{iid}_k'] = stoch_{iid}['slowk']",
        f"dataframe['{iid}_d'] = stoch_{iid}['slowd']",
    ]


def _emit_cci(iid: str, p: dict[str, Any]) -> list[str]:
    return [f"dataframe['{iid}'] = ta.CCI(dataframe, timeperiod={p['period']})"]


def _emit_mfi(iid: str, p: dict[str, Any]) -> list[str]:
    return [f"dataframe['{iid}'] = ta.MFI(dataframe, timeperiod={p['period']})"]


def _emit_volume_mean(iid: str, p: dict[str, Any]) -> list[str]:
    return [
        f"dataframe['{iid}'] = dataframe['volume']"
        f".rolling(window={p['period']}).mean()"
    ]


_PERIOD = lambda default, lo=2, hi=400: ParamDef("period", int, default, lo, hi)  # noqa: E731


CATALOG: dict[str, IndicatorDef] = {
    d.key: d
    for d in [
        IndicatorDef(
            key="rsi", label="RSI", category="momentum",
            description="Relative Strength Index, 0-100. Below 30 reads as oversold, above 70 as overbought.",
            params=(_PERIOD(14, 2, 200),),
            outputs=("{id}",), min_startup=30, emit=_emit_rsi,
        ),
        IndicatorDef(
            key="ema", label="EMA", category="trend",
            description="Exponential moving average; reacts faster than SMA to recent price.",
            params=(_PERIOD(21), ParamDef("source", str, "close", choices=PRICE_SOURCES)),
            outputs=("{id}",), min_startup=50, emit=_emit_ema,
        ),
        IndicatorDef(
            key="sma", label="SMA", category="trend",
            description="Simple moving average over the last N candles.",
            params=(_PERIOD(50), ParamDef("source", str, "close", choices=PRICE_SOURCES)),
            outputs=("{id}",), min_startup=50, emit=_emit_sma,
        ),
        IndicatorDef(
            key="macd", label="MACD", category="momentum",
            description="Moving average convergence/divergence. Emits the line, its signal and the histogram.",
            params=(
                ParamDef("fast", int, 12, 2, 100),
                ParamDef("slow", int, 26, 3, 200),
                ParamDef("signal", int, 9, 2, 100),
            ),
            outputs=("{id}", "{id}_signal", "{id}_hist"), min_startup=60, emit=_emit_macd,
        ),
        IndicatorDef(
            key="bbands", label="Bollinger Bands", category="volatility",
            description="Moving average with standard-deviation bands, plus the normalised channel width.",
            params=(_PERIOD(20, 5, 200), ParamDef("stddev", float, 2.0, 0.5, 5.0)),
            outputs=("{id}_lower", "{id}_middle", "{id}_upper", "{id}_width"),
            min_startup=40, emit=_emit_bbands,
        ),
        IndicatorDef(
            key="atr", label="ATR", category="volatility",
            description="Average true range; the usual building block for volatility-scaled stops.",
            params=(_PERIOD(14, 2, 100),),
            outputs=("{id}",), min_startup=30, emit=_emit_atr,
        ),
        IndicatorDef(
            key="adx", label="ADX", category="trend",
            description="Trend strength without direction. Above 25 commonly reads as trending.",
            params=(_PERIOD(14, 2, 100),),
            outputs=("{id}",), min_startup=40, emit=_emit_adx,
        ),
        IndicatorDef(
            key="stoch", label="Stochastic", category="momentum",
            description="Slow stochastic oscillator, emitting %K and %D.",
            params=(
                ParamDef("fastk", int, 14, 2, 100),
                ParamDef("slowk", int, 3, 1, 50),
                ParamDef("slowd", int, 3, 1, 50),
            ),
            outputs=("{id}_k", "{id}_d"), min_startup=40, emit=_emit_stoch,
        ),
        IndicatorDef(
            key="cci", label="CCI", category="momentum",
            description="Commodity channel index; typically read as extreme beyond +/-100.",
            params=(_PERIOD(20, 2, 200),),
            outputs=("{id}",), min_startup=40, emit=_emit_cci,
        ),
        IndicatorDef(
            key="mfi", label="Money Flow Index", category="volume",
            description="Volume-weighted RSI. Needs real volume data to mean anything.",
            params=(_PERIOD(14, 2, 100),),
            outputs=("{id}",), min_startup=30, emit=_emit_mfi,
        ),
        IndicatorDef(
            key="volume_mean", label="Average Volume", category="volume",
            description="Rolling mean of volume. Use it to require a signal happen on real participation.",
            params=(_PERIOD(20, 2, 200),),
            outputs=("{id}",), min_startup=40, emit=_emit_volume_mean,
        ),
    ]
}


def get(key: str) -> IndicatorDef:
    try:
        return CATALOG[key]
    except KeyError:
        raise ValueError(
            f"unknown indicator {key!r}; available: {', '.join(sorted(CATALOG))}"
        ) from None


def as_json() -> list[dict[str, Any]]:
    """Catalog rendered for the builder UI."""
    out = []
    for key, d in sorted(CATALOG.items()):
        out.append({
            "key": key,
            "label": d.label,
            "category": d.category,
            "description": d.description,
            "outputs": list(d.outputs),
            "params": [
                {
                    "name": p.name,
                    "type": p.kind.__name__,
                    "default": p.default,
                    "minimum": p.minimum,
                    "maximum": p.maximum,
                    "choices": list(p.choices) if p.choices else None,
                }
                for p in d.params
            ],
        })
    return out
