# The strategy spec

A strategy is written as JSON, not Python. The platform compiles it into a real
freqtrade `IStrategy` class.

That is a security boundary, not a convenience. The spec is a closed vocabulary:
indicators come from a fixed catalog, comparisons come from a fixed list, and
every identifier is checked against a regex before generation and again during
it. Nothing a user types ever reaches executable position, so a strategy cannot
run arbitrary code on the trading host.

## A complete example

```jsonc
{
  "spec_version": "1.0",
  "class_name": "MyPullback",        // ^[A-Z][A-Za-z0-9_]{2,63}$
  "description": "Buy pullbacks inside an uptrend",
  "timeframe": "5m",
  "can_short": false,

  "indicators": [
    { "id": "rsi14",    "kind": "rsi",         "params": { "period": 14 } },
    { "id": "ema_fast", "kind": "ema",         "params": { "period": 21 } },
    { "id": "ema_slow", "kind": "ema",         "params": { "period": 200 } },
    { "id": "vol",      "kind": "volume_mean", "params": { "period": 20 } }
  ],

  "entry": {
    "long": { "all": [
      { "left": "ema_fast", "op": "gt", "right": "ema_slow" },
      { "left": "rsi14",    "op": "lt", "right": { "const": 40 } },
      { "left": "volume",   "op": "gt", "right": "vol" }
    ]}
  },

  "exit": {
    "long": { "any": [
      { "left": "rsi14",    "op": "gt",            "right": { "const": 70 } },
      { "left": "ema_fast", "op": "crosses_below", "right": "ema_slow" }
    ]}
  },

  "risk": {
    "stoploss": -0.05,
    "minimal_roi": { "0": 0.05, "60": 0.02 },
    "trailing": { "enabled": true, "positive": 0.01, "offset": 0.02 }
  }
}
```

## Indicators

Each entry declares an `id` you reference in rules, a `kind` from the catalog,
and its `params`. `GET /api/catalog/indicators` returns the live catalog with
defaults and ranges.

| kind | Produces | Parameters |
|---|---|---|
| `rsi` | `{id}` | `period` |
| `ema`, `sma` | `{id}` | `period`, `source` |
| `macd` | `{id}`, `{id}_signal`, `{id}_hist` | `fast`, `slow`, `signal` |
| `bbands` | `{id}_lower`, `{id}_middle`, `{id}_upper`, `{id}_width` | `period`, `stddev` |
| `atr`, `adx`, `cci`, `mfi` | `{id}` | `period` |
| `stoch` | `{id}_k`, `{id}_d` | `fastk`, `slowk`, `slowd` |
| `volume_mean` | `{id}` | `period` |

An indicator with `id: "bb"` and `kind: "bbands"` produces `bb_lower`,
`bb_middle`, `bb_upper` and `bb_width`. Reference those names in rules.

The raw candle columns `open`, `high`, `low`, `close` and `volume` are always
available without declaring anything.

## Rules

A rule is either a **comparison** or a **group**.

A comparison has `left` (a column name), `op`, and `right` (a column name, a
`{"const": number}`, or for `between` a two-element `[low, high]`).

| op | Meaning |
|---|---|
| `gt`, `gte`, `lt`, `lte` | ordinary comparison |
| `crosses_above`, `crosses_below` | crossed on this candle, not merely above or below |
| `between` | inclusive range |

A group is `{"all": [...]}`, `{"any": [...]}` or `{"not": [...]}` — logical AND,
OR, and "none of these hold". Groups nest.

```jsonc
{ "all": [
    { "left": "adx", "op": "gt", "right": { "const": 25 } },
    { "any": [
        { "left": "macd",  "op": "gt", "right": "macd_signal" },
        { "left": "rsi14", "op": "lt", "right": { "const": 35 } }
    ]},
    { "not": [ { "left": "cci", "op": "gt", "right": { "const": 200 } } ] }
]}
```

## Risk

`stoploss` must be negative and greater than `-1`. `minimal_roi` maps
minutes-since-entry to a profit target and must include a `"0"` key.

Trailing stops are validated more strictly than freqtrade does: `offset` must
exceed `positive`, because otherwise the stop trails from below its own trigger
and freqtrade rejects the config at startup with an opaque error.

## Things the validator refuses

These all fail with a message naming the problem, before any code is generated:

- a `class_name` that is not a Python identifier starting uppercase
- an indicator `kind` outside the catalog
- a rule referencing a column no indicator produces
- two indicators sharing an `id`
- a parameter outside its documented range, or one that does not exist
- `entry.short` when `can_short` is false
- a group setting more than one of `all` / `any` / `not`
- `minimal_roi` without a `"0"` entry
- `between` with `low >= high`

## Warm-up

`startup_candle_count` is derived from the slowest indicator in the spec rather
than guessed. An EMA(200) asks for more history than an EMA(9), so a strategy
never silently trades on indicator values that have not converged.

Override it with an explicit `startup_candle_count` if you have a reason.

## Compiling

`POST /api/strategies/compile` with `{"spec": {...}}` validates, generates, then
**imports the generated module and runs all three populate methods** against a
deterministic price series. The response includes the generated Python and how
many entry and exit signals it produced.

A strategy that generates zero signals on that series is worth a second look
before spending a backtest on it.
