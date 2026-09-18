# The Learning Module

An append-only record of what the bot saw, what it wanted to do, what it did,
and why -- kept next to Verification, which proves what happened on the
exchange, and never mixed with it.

It never trades, never edits a strategy, and cannot stop the bot trading:
every record goes into a local queue in well under a millisecond, and a
background writer ships the queue to Supabase on its own schedule.

## What exists today

| Phase | What | State |
|---|---|---|
| 1 | Contracts: the decision record, the event record, ids, keys, canonical form (`app/learning/contracts.py`, `ids.py`, `keys.py`, `canonical.py`, `provenance.py`) | done |
| 2 | Storage: the evidence tables (`0027`), the health table and view (`0028`), the outbox and writer (`app/learning/outbox.py`, `writer.py`) | done |
| 3 | Capture: `app/learning/freqtrade_adapter.py` hooks freqtrade's own classes (signals, locks, stake checks, entries, exits, orders, fills, RPC messages) and records decisions the moment the bot acts; `recorder.py` and `snapshots.py` are the freqtrade-free core | done |
| 4 | Lifecycle: orders, fills and positions linked back to their decision; the link is kept in freqtrade's own `trade_custom_data`, so it survives a redeploy; a position the module did not see opened is recorded as `unlinked_position_observed`, never given an invented decision | done |
| 5 | Verification linkage: `verification_link.py` writes every reconciliation run's verdicts as events on the decisions that placed the orders; the Verification tab's chain names the decision and the rejection reason | done |
| 6 | The Trading memory tab and `/api/learning`: the raw JSON of every record, with a sentence for every field, stage, reason and number | done |

Nothing is captured unless `LEARNING_ENABLED` is `true` on the bot. It is
off in production and on for the staging soak. Everything the module
records for a strategy that was not touched: the hooks sit on freqtrade's
classes, not on the strategy file.

## How a decision is captured

| freqtrade does | the adapter records |
|---|---|
| `get_entry_signal` returns a signal | opens the decision (one per pair per candle) and `signal_generated` |
| `is_pair_locked` says yes | `signal_rejected` PAIR_LOCKED, with the lock |
| the wallet refuses or shrinks the stake | `signal_rejected` INSUFFICIENT_BALANCE / INSUFFICIENT_STAKE / MIN_NOTIONAL |
| `execute_entry` / `execute_trade_exit` run | `bot_instruction_created` with stake, price and reason |
| `Exchange.create_order` runs | `order_submitted`, then `order_acknowledged` or `order_rejected` |
| the ENTRY message names the trade | the trade is linked to the decision and the link written to `trade_custom_data` |
| `order_filled` fires | `fill_completed` (or `partial_fill`), with both order ids |
| ENTRY_FILL / EXIT_FILL messages | `position_opened`, `position_adjusted`, `position_closed` |
| a pass ends with signals the bot never brought to `create_trade` | a decision per skipped signal, rejected MAX_OPEN_TRADES, GLOBAL_PAIRLOCK, POSITION_ALREADY_OPEN or BOT_PAUSED, marked `derivation: whitelist_scan` |
| protections fire, the bot changes state | `risk_decision`, `bot_status`, with no decision |

Every hook passes freqtrade's result and exceptions through untouched and
counts its own failures (`adapter_errors` in the health row and the log).

## The two tables

**`trading_decisions`** -- one row per decision, as the bot saw it at the moment
it decided: the candle it was looking at (`market_context`), every indicator
value the strategy had (`feature_snapshot`), what it held and how many slots
were free (`portfolio_snapshot`), what was locked and what the stop was
(`risk_snapshot`), and which exact code, parameters and configuration were
running (`provenance`, `strategy_code_hash`, `parameter_hash`,
`runtime_config_hash`). `decision_id` is a UUIDv7, so sorting by id sorts by
time.

**`trading_events`** -- what happened to each decision afterwards, one row per
step: `signal_generated`, `signal_rejected` (with a `rejection_code` that says
why), `order_submitted`, `order_acknowledged`, `order_rejected`, `partial_fill`,
`fill_completed`, `position_opened`, `position_closed`, and references to the
verifier's findings. `decision_id` is null only for activity the bot never
decided on -- an order it did not place, its own status -- and is never
invented to make a row fit.

## Three rules, enforced by the database

**Append-only.** No role -- not the dashboard, not the bot, not the worker --
may update or delete a row. The grants say so and a trigger says so again, so
a careless future grant still fails loudly. A correction is a new row.

**One row per logical record.** `idempotency_key` is unique, and the writer
inserts with `ON CONFLICT DO NOTHING`, so a record captured twice -- a
restart, a replay, freqtrade re-evaluating the same candle every five seconds
-- is one row. A decision is keyed on the candle it was made on; an event on
its decision, type and second.

**Retention is a decision.** `prune_learning_records(p_keep_days)` is the one
deletion path. It runs as the service role, sets a transaction-local flag the
trigger checks, and returns how many rows it removed.

## How a record travels

```
freqtrade callback --> outbox.enqueue()      one INSERT into a local SQLite file, never raises
                         |
                  LearningWriter thread      every 2 s: claim rows in order, POST to Supabase
                         |
                  trading_decisions / trading_events
```

The outbox hands rows out strictly in order. A row that is backing off holds
everything behind it, which is what keeps an event from ever reaching the
database before its decision.

The writer tells two failures apart. When the database cannot be reached or
refuses everything (a connection error, a timeout, a 5xx, a bad key, a missing
table) nothing is wrong with the rows: the writer pauses, doubling its wait up
to a minute, and tries the same rows again. No row is charged for an outage.
When the database refuses a row (400, 409, 422) the batch is sent one row at a
time to find the culprit; the good rows go through, the bad one is backed off
and, after ten refusals, quarantined -- counted and kept in the outbox, never
dropped.

If SQLite itself fails -- disk full, a corrupt file -- a circuit breaker drops
records for a minute and counts them (`dropped_total`), because a learning
record is worth a great deal less than an uninterrupted trading loop.

## Health

The writer publishes one row per bot to `learning_writer_status` (pending,
oldest age, quarantined, written, failed, retried, dropped, outages, last
error). `v_learning_health` joins it to what actually arrived in the last 24
hours -- `decisions_24h`, `events_24h`, `events_without_decision_24h`,
`decisions_without_events_24h`, `last_decision_at` -- so "the writer says it
is fine" and "decisions are arriving" are shown together, and a pipeline that
died quietly shows as a fresh status row next to a stale `last_decision_at`.

## Reading it

The **Trading memory** tab on the dashboard lists every decision with a
one-line headline ("Wanted to enter TRX/USDT on the 08:00 candle, tagged
pullback, but did not: the pair was locked by a protection"), filters by
pair, kind, outcome and indicator value, and opens any decision to show
the full raw record, a timeline of its events with a sentence per stage,
and a glossary entry for every key. The same data is at `/api/learning/...`
for anything that prefers JSON.

The worker checks the pipeline every five minutes and opens a
`learning_stalled` incident on the Live bot tab when records pile up on the
bot, when the database refuses them, or when the strategy keeps signalling
while no decision has been recorded for a day. It prunes records past
retention once a day.

## Exercising it

`scripts/learning_smoke.py` is the synthetic source: it ships one made-up
decision and its five events through the real writer, ships them again from a
fresh outbox to prove a replay writes nothing new, and reads back what landed.
The rows are unmistakably fake (`environment = smoke`, `SMOKE/USDT`,
`SmokeStrategy`, no owner) and it refuses to run against production.

```
SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=... python scripts/learning_smoke.py --environment staging
```

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `LEARNING_ENABLED` | `false` | Capture decisions inside the bot |
| `LEARNING_OUTBOX_PATH` | `user_data/learning_outbox.sqlite` | The local queue |
| `LEARNING_WRITE_INTERVAL_SECONDS` | `2` | How often the writer ships |

## What is deliberately not done

No decisions are invented for trades that predate the module, and none are
back-filled from history: a decision record is evidence of what the bot saw,
and there is no honest way to reconstruct that afterwards. Outcome labels,
horizons, counterfactuals and models are defined as contracts
(`HorizonProfile`, `OutcomeLabel`, the `model_*` columns) and left empty until
their phases are built.
