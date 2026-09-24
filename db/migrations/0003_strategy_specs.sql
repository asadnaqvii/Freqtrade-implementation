-- 0003_strategy_specs.sql
-- The strategy builder. A strategy is authored as a declarative JSON spec
-- ("the predefined format"), versioned immutably, and compiled into a real
-- freqtrade IStrategy python file by the platform's code generator.
--
-- Users never write python here. That is the point: the spec is a closed
-- vocabulary of indicators and comparisons, so a generated strategy cannot
-- execute arbitrary code on the trading host.

-- ---------------------------------------------------------------------------
-- indicator_catalog: the building blocks the builder UI offers
-- ---------------------------------------------------------------------------
create table if not exists public.indicator_catalog (
  key             text primary key,                      -- 'rsi', 'ema', 'bbands', ...
  label           text not null,
  category        text not null                          -- 'momentum' | 'trend' | 'volatility' | 'volume' | 'price'
                    check (category in ('momentum', 'trend', 'volatility', 'volume', 'price', 'custom')),
  description     text,
  -- JSON Schema fragment describing this indicator's parameters, so the UI can
  -- render inputs and the API can validate without hardcoding a form per indicator.
  params_schema   jsonb not null default '{}'::jsonb,
  -- Column names this indicator contributes to the dataframe, templated with
  -- its params: e.g. ['rsi_{period}'] -> 'rsi_14'.
  outputs         text[] not null default '{}',
  -- Minimum candles the indicator needs before its output is trustworthy.
  min_startup_candles integer not null default 0,
  enabled         boolean not null default true,
  sort_order      integer not null default 100,
  created_at      timestamptz not null default now()
);

comment on table public.indicator_catalog is
  'Closed vocabulary of indicators the strategy builder can emit. Adding a row here is how you extend the builder.';

-- ---------------------------------------------------------------------------
-- strategy_specs: the user-facing container
--
-- Named strategy_specs, not strategies, because this project's Postgres already
-- has a public.strategies from an earlier effort that records which freqtrade
-- strategy is currently active. Different concept, same obvious name. Colliding
-- with it would have been silent -- `create table if not exists` would keep the
-- old table and every FK and policy below would bind to the wrong one.
-- ---------------------------------------------------------------------------
create table if not exists public.strategy_specs (
  id                 uuid primary key default gen_random_uuid(),
  owner_id           uuid references public.profiles (id) on delete cascade,
  name               text not null,
  -- The python class name that gets generated. Constrained because it lands in
  -- a file name and an import path.
  class_name         text not null
                       check (class_name ~ '^[A-Z][A-Za-z0-9_]{2,63}$'),
  description        text,
  source             public.strategy_source not null default 'builder',
  is_archived        boolean not null default false,
  is_public          boolean not null default false,      -- shareable read-only template
  tags               text[] not null default '{}',
  current_version_id uuid,                                -- FK added after strategy_versions exists
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),

  constraint strategy_specs_name_unique unique (owner_id, name),
  constraint strategy_specs_class_unique unique (owner_id, class_name)
);

comment on table public.strategy_specs is
  'Authored strategy definitions. Distinct from the legacy public.strategies table, which tracks the active strategy.';

create index if not exists strategy_specs_owner_idx on public.strategy_specs (owner_id) where not is_archived;
create index if not exists strategy_specs_public_idx on public.strategy_specs (is_public) where is_public;

drop trigger if exists strategy_specs_set_updated_at on public.strategy_specs;
create trigger strategy_specs_set_updated_at
  before update on public.strategy_specs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- strategy_versions: immutable snapshots
-- ---------------------------------------------------------------------------
create table if not exists public.strategy_versions (
  id             uuid primary key default gen_random_uuid(),
  strategy_id    uuid not null references public.strategy_specs (id) on delete cascade,
  version        integer not null,
  -- The declarative spec. Validated against the pydantic model in
  -- app/strategy_builder/spec.py before it is ever written here.
  spec           jsonb not null,
  spec_version   text not null default '1.0',            -- format version, for future migrations
  -- Output of the code generator. Kept so a backtest result can always be
  -- traced to the exact python that produced it, even if the generator changes.
  generated_code text,
  code_sha256    text,
  -- Did the generated code import and instantiate cleanly?
  compiles       boolean,
  compile_error  text,
  notes          text,
  created_by     uuid references public.profiles (id) on delete set null,
  created_at     timestamptz not null default now(),

  constraint strategy_versions_unique unique (strategy_id, version)
);

create index if not exists strategy_versions_strategy_idx
  on public.strategy_versions (strategy_id, version desc);

do $$
begin
  if not exists (select 1 from information_schema.table_constraints
                 where constraint_name = 'strategy_specs_current_version_fkey'
                   and table_schema = 'public') then
    alter table public.strategy_specs
      add constraint strategy_specs_current_version_fkey
      foreign key (current_version_id) references public.strategy_versions (id) on delete set null;
  end if;
end $$;

-- Versions are append-only: the whole point is that a backtest can be traced
-- back to unchanged source. Block UPDATE and DELETE on the spec/code columns.
create or replace function public.strategy_versions_immutable()
returns trigger
language plpgsql
as $$
begin
  if tg_op = 'DELETE' then
    raise exception 'strategy_versions rows are immutable; archive the strategy instead';
  end if;
  if new.spec is distinct from old.spec
     or new.generated_code is distinct from old.generated_code
     or new.version is distinct from old.version
     or new.strategy_id is distinct from old.strategy_id then
    raise exception 'strategy_versions.spec/generated_code/version are immutable; create a new version';
  end if;
  return new;
end;
$$;

drop trigger if exists strategy_versions_guard on public.strategy_versions;
create trigger strategy_versions_guard
  before update or delete on public.strategy_versions
  for each row execute function public.strategy_versions_immutable();

-- Allocate the next version number for a strategy without a race.
create or replace function public.next_strategy_version(p_strategy_id uuid)
returns integer
language plpgsql
as $$
declare
  v integer;
begin
  -- Lock the parent row so two concurrent saves cannot claim the same number.
  perform 1 from public.strategy_specs where id = p_strategy_id for update;
  select coalesce(max(version), 0) + 1 into v
    from public.strategy_versions where strategy_id = p_strategy_id;
  return v;
end;
$$;
