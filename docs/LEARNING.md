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
| 3 | Capture: an adapter that records decisions at the moment freqtrade acts, without touching the strategy files | next |
| 4 | Lifecycle: orders, fills and positions linked back to their decision, surviving restarts | next |
| 5 | Verification linkage: every reconciliation run referenced from the decision it checked | next |
| 6 | The Trading memory tab: raw JSON of every record, with everything around it explained | next |

Nothing is captured until phase 3 lands, so the tables stay empty in every
environment until then. `LEARNING_ENABLED` is `false` everywhere.

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
| `LEARNING_ENABLED` | `false` | Capture decisions inside the bot (phase 3) |
| `LEARNING_OUTBOX_PATH` | `user_data/learning_outbox.sqlite` | The local queue |
| `LEARNING_WRITE_INTERVAL_SECONDS` | `2` | How often the writer ships |

## What is deliberately not done

No decisions are invented for trades that predate the module, and none are
back-filled from history: a decision record is evidence of what the bot saw,
and there is no honest way to reconstruct that afterwards. Outcome labels,
horizons, counterfactuals and models are defined as contracts
(`HorizonProfile`, `OutcomeLabel`, the `model_*` columns) and left empty until
their phases are built.
