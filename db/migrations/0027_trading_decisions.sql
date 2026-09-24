-- 0027_trading_decisions.sql
--
-- The Learning Module's evidence: one row per trading decision, as the bot
-- saw it at the moment it decided, and an append-only stream of what
-- happened to that decision afterwards.
--
-- A normal trading database stores the result. These tables store the
-- evidence: the exact indicator values the strategy had, the candle it was
-- looking at, what it held, what was locked, which code and parameters were
-- running -- and then every order, acknowledgement, fill and position change
-- that followed, each linked back by decision_id. Verification (0005, 0017)
-- keeps proving what happened on the exchange; this records why the bot
-- acted, so the two can be read together without either being rewritten.
--
-- Three rules, all enforced here rather than trusted to code:
--
--   Append-only. No role -- not the dashboard, not the bot, not the worker --
--   may update or delete a row. A correction is a new row. The grants say so
--   and a trigger says so again, so a careless future grant still fails
--   loudly. The one deletion path is prune_learning_records(), which sets a
--   session flag the trigger checks, so retention is a decision, not a
--   habit.
--
--   One row per logical record. idempotency_key is unique, and the writer
--   inserts with ON CONFLICT DO NOTHING, so a record captured twice -- a
--   restart, a replay, freqtrade re-evaluating the same candle every five
--   seconds -- is one row.
--
--   No foreign key from an event to its decision. PostgREST cannot write two
--   tables in one transaction; a key here would let one failed decision
--   insert wedge every event behind it in the outbox forever. The writer
--   ships in order instead, and v_learning_health (0028) counts events with
--   no decision so a gap is visible within a minute.
--
-- decision_id is a UUIDv7, so sorting by id is sorting by time. Column
-- names follow this repository (ft_trade_id, exchange_order_id), not the
-- handoff document's, so they join v_live_orders and order_reconciliations
-- without renaming.

do $$
begin
  if not exists (select 1 from pg_type where typname = 'decision_kind') then
    create type public.decision_kind as enum ('entry', 'exit', 'add', 'reduce');
  end if;
  if not exists (select 1 from pg_type where typname = 'rejection_stage') then
    create type public.rejection_stage as enum
      ('strategy', 'bot', 'risk', 'execution', 'exchange', 'system');
  end if;
end $$;

create table if not exists public.trading_decisions (
  decision_id          uuid primary key,
  schema_version       smallint not null default 1,
  decision_time_utc    timestamptz not null,
  recorded_at_utc      timestamptz not null default now(),
  environment          text not null,
  run_id               uuid,
  owner_id             uuid references public.profiles(id) on delete cascade,
  bot_instance_id      uuid references public.bot_instances(id) on delete set null,
  account_id           uuid references public.exchange_accounts(id) on delete set null,
  exchange             text not null,
  symbol               text not null,
  market_type          text not null default 'spot',
  timeframe            text not null,
  decision_kind        public.decision_kind not null,
  strategy_intent      text not null,
  position_id          text,
  strategy_id          text not null,
  strategy_version     text,
  strategy_code_hash   text,
  parameter_version    text,
  parameter_hash       text,
  runtime_config_hash  text,
  feature_set_version  text not null,
  model_id             text,
  model_version        text,
  entry_tag            text,
  exit_reason          text,
  market_data_max_ts   timestamptz,
  market_context       jsonb not null default '{}'::jsonb,
  feature_snapshot     jsonb not null default '{}'::jsonb,
  portfolio_snapshot   jsonb not null default '{}'::jsonb,
  risk_snapshot        jsonb not null default '{}'::jsonb,
  prediction_snapshot  jsonb,
  provenance           jsonb not null default '{}'::jsonb,
  quarantined          boolean not null default false,
  quarantine_reason    text,
  idempotency_key      text not null unique,
  payload_sha256       text not null
);

comment on table public.trading_decisions is
  'One row per trading decision, as the bot saw it when it decided. Append-only evidence; never edited.';

create table if not exists public.trading_events (
  event_id           uuid primary key,
  schema_version     smallint not null default 1,
  decision_id        uuid,
  owner_id           uuid references public.profiles(id) on delete cascade,
  bot_instance_id    uuid references public.bot_instances(id) on delete set null,
  event_type         text not null,
  event_time_utc     timestamptz not null,
  recorded_at_utc    timestamptz not null default now(),
  event_source       text not null,
  symbol             text,
  position_id        text,
  ft_trade_id        bigint,
  ft_order_id        bigint,
  exchange_order_id  text,
  rejection_stage    public.rejection_stage,
  rejection_code     text,
  payload            jsonb not null default '{}'::jsonb,
  idempotency_key    text not null unique,
  payload_sha256     text not null
);

comment on table public.trading_events is
  'What happened to each decision afterwards: orders, fills, positions, rejections, verifier references. Append-only.';
comment on column public.trading_events.decision_id is
  'Null only for activity the bot never decided on (an unaccounted exchange order, its own status). Never invented to make a row fit.';

create index if not exists trading_decisions_time_idx on public.trading_decisions (decision_time_utc desc);
create index if not exists trading_decisions_bot_time_idx on public.trading_decisions (bot_instance_id, decision_time_utc desc);
create index if not exists trading_decisions_symbol_time_idx on public.trading_decisions (symbol, decision_time_utc desc);
create index if not exists trading_decisions_strategy_idx on public.trading_decisions (strategy_id, strategy_version);
create index if not exists trading_decisions_kind_idx on public.trading_decisions (decision_kind, strategy_intent);
create index if not exists trading_decisions_position_idx on public.trading_decisions (position_id);
create index if not exists trading_decisions_owner_time_idx on public.trading_decisions (owner_id, decision_time_utc desc);
create index if not exists trading_decisions_features_idx on public.trading_decisions using gin (feature_snapshot jsonb_path_ops);

create index if not exists trading_events_decision_idx on public.trading_events (decision_id);
create index if not exists trading_events_time_idx on public.trading_events (event_time_utc desc);
create index if not exists trading_events_type_time_idx on public.trading_events (event_type, event_time_utc desc);
create index if not exists trading_events_trade_idx on public.trading_events (ft_trade_id);
create index if not exists trading_events_exchange_order_idx on public.trading_events (exchange_order_id);
create index if not exists trading_events_position_idx on public.trading_events (position_id);
create index if not exists trading_events_owner_time_idx on public.trading_events (owner_id, event_time_utc desc);

-- Append-only, enforced twice. The grants are the real control; the trigger
-- is there so a careless future grant still fails loudly. Retention goes
-- through prune_learning_records() and nothing else.
create or replace function public.learning_is_append_only()
returns trigger
language plpgsql
as $$
begin
  if current_setting('learning.allow_prune', true) = 'on' and tg_op = 'DELETE' then
    return old;
  end if;
  raise exception 'learning records are append-only: % on % is refused', tg_op, tg_table_name
    using hint = 'record a correction as a new row; retention runs through prune_learning_records()';
end;
$$;

drop trigger if exists trading_decisions_append_only on public.trading_decisions;
create trigger trading_decisions_append_only
  before update or delete on public.trading_decisions
  for each row execute function public.learning_is_append_only();

drop trigger if exists trading_events_append_only on public.trading_events;
create trigger trading_events_append_only
  before update or delete on public.trading_events
  for each row execute function public.learning_is_append_only();

create or replace function public.prune_learning_records(p_keep_days integer default 365)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  removed integer := 0;
  n integer;
begin
  perform set_config('learning.allow_prune', 'on', true);
  delete from public.trading_events
   where event_time_utc < now() - make_interval(days => p_keep_days);
  get diagnostics n = row_count;
  removed := removed + n;
  delete from public.trading_decisions
   where decision_time_utc < now() - make_interval(days => p_keep_days);
  get diagnostics n = row_count;
  removed := removed + n;
  perform set_config('learning.allow_prune', 'off', true);
  return removed;
end;
$$;

revoke all on function public.prune_learning_records(integer) from public, anon, authenticated;
grant execute on function public.prune_learning_records(integer) to service_role;

-- Row level security: owners read their own; nobody writes through the API
-- except the bot and worker, which hold the service key. A dashboard that
-- could invent decisions could hide a missed trade -- the same reasoning as
-- 0017's strategy_signals.
alter table public.trading_decisions enable row level security;
alter table public.trading_decisions force row level security;
alter table public.trading_events enable row level security;
alter table public.trading_events force row level security;

drop policy if exists trading_decisions_owner_read on public.trading_decisions;
create policy trading_decisions_owner_read on public.trading_decisions
  for select to authenticated
  using (owner_id = (select auth.uid()));

drop policy if exists trading_events_owner_read on public.trading_events;
create policy trading_events_owner_read on public.trading_events
  for select to authenticated
  using (owner_id = (select auth.uid()));

-- Supabase's default privileges hand every new table to anon, authenticated
-- and service_role in full. Take back everything but what each needs.
revoke all on public.trading_decisions from anon;
revoke all on public.trading_events from anon;
revoke insert, update, delete, truncate, references, trigger on public.trading_decisions from authenticated;
revoke insert, update, delete, truncate, references, trigger on public.trading_events from authenticated;
revoke update, delete, truncate, references, trigger on public.trading_decisions from service_role;
revoke update, delete, truncate, references, trigger on public.trading_events from service_role;
grant select on public.trading_decisions, public.trading_events to authenticated;
grant select, insert on public.trading_decisions, public.trading_events to service_role;
