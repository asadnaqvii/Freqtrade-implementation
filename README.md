# Freqtrade Trading Platform

A freqtrade deployment with a database behind it, a strategy builder in front of
it, and verification that runs against your own wallet.

## What this is

The bot trades. Everything else exists because a bot on its own leaves you
guessing:

- **Persistence.** Freqtrade writes to a SQLite file inside its container. On
  Render and Railway that filesystem does not survive a redeploy, so trade
  history vanished on every deploy. It now lives in Supabase Postgres.
- **Strategy builder.** Strategies are authored as JSON and compiled into real
  freqtrade `IStrategy` classes. No Python, and no way for a strategy to run
  arbitrary code on the trading host.
- **Backtesting.** Runs freqtrade's own backtesting engine — any exchange, any
  pairs, any quote currency, any past window — and stores the results.
- **Verification.** Checks your exchange connection, your keys, your balances
  and your open orders, against your own wallet with your own credentials.

## Architecture

```
internet ──▶ freqtrade-app     web service, public        API + dashboard
                  │ private network
                  ├──▶ freqtrade-bot    private service   trades, holds the keys
                  └──▶ freqtrade-worker background worker runs backtests
                             │
                       all three ──▶ Supabase Postgres
```

The bot has **no public URL**. It is the only process holding exchange API keys
and the only one that can place an order, so nothing on the internet reaches it.
The app service is the front door and reads the bot's state from the database.

One Postgres, two schemas: `ft_main` holds the tables freqtrade creates and owns,
reached only over raw Postgres and never exposed to the API. `public` holds the
application tables, every one RLS-protected and owner-scoped.

## Getting started

1. **Apply the schema.** `db/migrations/0001` … `0012`, in order.
2. **Move your existing trade history** before pointing anything at Postgres —
   see [`docs/DATA_MIGRATION.md`](docs/DATA_MIGRATION.md).
3. **Deploy.** `render.yaml` is a complete blueprint. See
   [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md).
4. **Sign in, connect your wallet, run a verification.**

### If you are deploying to Render and using KuCoin

Use a **non-US region**. KuCoin blocks US IP addresses and Render defaults to
Oregon, which is why this project worked on Railway and failed on Render. The
blueprint pins every service to `singapore`. A Render service's region cannot be
changed after it is created, so getting this right the first time saves deleting
and recreating the service.

## Writing a strategy

```jsonc
{
  "class_name": "MyPullback",
  "timeframe": "5m",
  "indicators": [
    { "id": "rsi14",    "kind": "rsi", "params": { "period": 14 } },
    { "id": "ema_fast", "kind": "ema", "params": { "period": 21 } },
    { "id": "ema_slow", "kind": "ema", "params": { "period": 200 } }
  ],
  "entry": { "long": { "all": [
    { "left": "ema_fast", "op": "gt", "right": "ema_slow" },
    { "left": "rsi14",    "op": "lt", "right": { "const": 40 } }
  ]}},
  "exit": { "long": { "any": [
    { "left": "rsi14", "op": "gt", "right": { "const": 70 } }
  ]}},
  "risk": { "stoploss": -0.05, "minimal_roi": { "0": 0.05, "60": 0.02 } }
}
```

Compiling this imports the generated module and runs it against a test series,
so a broken strategy fails while you are looking at it rather than mid-backtest.
Full reference: [`docs/STRATEGY_FORMAT.md`](docs/STRATEGY_FORMAT.md).

## Verification

Every check runs with your credentials against the venue you actually trade on.
The platform holds no funds and has no house account. Any of the ~100 exchanges
ccxt supports works; KuCoin gets extra handling for its passphrase, its
geo-blocking and its clock-skew rejection.

| Check | Catches |
|---|---|
| `provider.geo_block` | The venue refused where you are — distinct from the venue being down, because the fix is completely different |
| `provider.permissions` | **Fails** a key that can withdraw. A trading bot never needs it, and it turns a bot compromise into a funds loss |
| `provider.clock_skew` | Signed requests about to be rejected for a clock a few seconds out |
| `market.min_notional` | A stake below the venue's minimum, where orders are rejected one by one and the only symptom is that nothing fills |
| `balance.sufficient` | Not enough free balance to fund the configured slots |
| `reconciliation.*` | Your bot's records disagreeing with the exchange's, including orders on your account the bot never placed |

## Layout

```
app/
  api/               FastAPI service and the built-in dashboard
  strategy_builder/  spec, catalog, code generator, compile checker
  providers/         wallet providers: ccxt, kucoin, paper
  validation/        verification engine, checks, reconciliation
  backtest/          freqtrade runner and result parser
  worker/            backtest queue worker
  core/              config, Supabase clients, auth
db/migrations/       schema, RLS, hardening
scripts/             data migration
strategies/          hand-written strategies, still usable
tests/
```

## Development

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/ -q
```

The tests cover hostile strategy specs, every provider error-translation path,
each verification check's failure mode, and the backtest parser against the field
names freqtrade actually emits. They need no network and no credentials.

## Disclaimer

Cryptocurrency trading carries real risk of loss. Backtest results describe the
past and are not a forecast. Run in dry-run mode first, and only trade money you
can afford to lose.

MIT licensed — see `LICENSE`.
