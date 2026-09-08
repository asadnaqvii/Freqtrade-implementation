-- 0004_backtesting.sql
-- Backtest jobs (a queue the worker drains) and the results they produce.

-- ---------------------------------------------------------------------------
-- backtest_jobs: the work queue
-- ---------------------------------------------------------------------------
create table if not exists public.backtest_jobs (
  id                  uuid primary key default gen_random_uuid(),
  owner_id            uuid references public.profiles (id) on delete cascade,
  strategy_id         uuid references public.strategy_specs (id) on delete set null,
  strategy_version_id uuid references public.strategy_versions (id) on delete set null,
  -- Set instead of strategy_version_id when backtesting one of the repo's
  -- built-in strategy files rather than a builder-authored one.
  builtin_strategy    text,

  exchange            text not null default 'kucoin',
  timeframe           text not null default '5m',
  pairs               text[] not null default '{}',
  timerange           text,                              -- freqtrade syntax, e.g. '20250101-20250601'
  stake_currency      text not null default 'USDT',
  stake_amount        numeric(38, 18),
  starting_balance    numeric(38, 18) not null default 1000,
  max_open_trades     integer not null default 3,
  fee                 numeric(12, 8),
  enable_protections  boolean not null default false,
  download_data       boolean not null default true,
  extra_args          jsonb not null default '{}'::jsonb,

  status              public.run_status not null default 'queued',
  priority            integer not null default 100,
  attempts            integer not null default 0,
  max_attempts        integer not null default 3,
  claimed_by          text,
  claimed_at          timestamptz,
  heartbeat_at        timestamptz,
  started_at          timestamptz,
  finished_at         timestamptz,
  error               text,
  progress            text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),

  -- Every job needs exactly one strategy source.
  constraint backtest_jobs_strategy_source
    check ((strategy_version_id is not null) <> (builtin_strategy is not null)),
  constraint backtest_jobs_pairs_not_empty check (cardinality(pairs) > 0)
);

create index if not exists backtest_jobs_queue_idx
  on public.backtest_jobs (status, priority, created_at)
  where status in ('queued', 'running');
create index if not exists backtest_jobs_owner_idx on public.backtest_jobs (owner_id, created_at desc);

drop trigger if exists backtest_jobs_set_updated_at on public.backtest_jobs;
create trigger backtest_jobs_set_updated_at
  before update on public.backtest_jobs
  for each row execute function public.set_updated_at();

-- ---------------------------------------------------------------------------
-- backtest_runs: headline results
-- ---------------------------------------------------------------------------
create table if not exists public.backtest_runs (
  id                    uuid primary key default gen_random_uuid(),
  job_id                uuid references public.backtest_jobs (id) on delete cascade,
  owner_id              uuid references public.profiles (id) on delete cascade,
  strategy_id           uuid references public.strategy_specs (id) on delete set null,
  strategy_version_id   uuid references public.strategy_versions (id) on delete set null,
  strategy_name         text not null,
  -- sha256 of the exact strategy source that ran. Two runs with the same
  -- fingerprint and the same window are comparable; different fingerprints are not.
  strategy_fingerprint  text,

  exchange              text not null,
  timeframe             text not null,
  pairs                 text[] not null default '{}',
  timerange_start       timestamptz,
  timerange_end         timestamptz,
  stake_currency        text not null default 'USDT',
  starting_balance      numeric(38, 18),
  final_balance         numeric(38, 18),
  max_open_trades       integer,

  total_trades          integer not null default 0,
  wins                  integer not null default 0,
  losses                integer not null default 0,
  draws                 integer not null default 0,
  win_rate              numeric(10, 6),
  profit_total_abs      numeric(38, 18),
  profit_total_pct      numeric(18, 8),
  profit_factor         numeric(18, 8),
  expectancy            numeric(38, 18),
  expectancy_ratio      numeric(18, 8),
  cagr                  numeric(18, 8),
  sharpe                numeric(18, 8),
  sortino               numeric(18, 8),
  calmar                numeric(18, 8),
  max_drawdown_abs      numeric(38, 18),
  max_drawdown_pct      numeric(18, 8),
  max_drawdown_start    timestamptz,
  max_drawdown_end      timestamptz,
  avg_trade_duration_min numeric(18, 4),
  best_pair             text,
  worst_pair            text,
  trades_per_day        numeric(18, 6),

  freqtrade_version     text,
  config                jsonb not null default '{}'::jsonb,
  raw_metrics           jsonb not null default '{}'::jsonb,   -- full freqtrade result blob
  started_at            timestamptz,
  finished_at           timestamptz,
  duration_seconds      numeric(18, 3),
  created_at            timestamptz not null default now()
);

create index if not exists backtest_runs_owner_idx on public.backtest_runs (owner_id, created_at desc);
create index if not exists backtest_runs_strategy_idx on public.backtest_runs (strategy_id, created_at desc);
create index if not exists backtest_runs_job_idx on public.backtest_runs (job_id);

-- ---------------------------------------------------------------------------
-- backtest_pair_results / backtest_trades / backtest_equity_curve
-- ---------------------------------------------------------------------------
create table if not exists public.backtest_pair_results (
  id               bigint generated by default as identity primary key,
  run_id           uuid not null references public.backtest_runs (id) on delete cascade,
  pair             text not null,
  trades           integer not null default 0,
  wins             integer not null default 0,
  losses           integer not null default 0,
  draws            integer not null default 0,
  profit_abs       numeric(38, 18),
  profit_pct       numeric(18, 8),
  profit_mean_pct  numeric(18, 8),
  profit_sum_pct   numeric(18, 8),
  duration_avg_min numeric(18, 4),
  constraint backtest_pair_results_unique unique (run_id, pair)
);

create table if not exists public.backtest_trades (
  id               bigint generated by default as identity primary key,
  run_id           uuid not null references public.backtest_runs (id) on delete cascade,
  pair             text not null,
  is_short         boolean not null default false,
  open_date        timestamptz,
  close_date       timestamptz,
  open_rate        numeric(38, 18),
  close_rate       numeric(38, 18),
  amount           numeric(38, 18),
  stake_amount     numeric(38, 18),
  profit_abs       numeric(38, 18),
  profit_ratio     numeric(18, 8),
  trade_duration_min integer,
  enter_tag        text,
  exit_reason      text,
  fee_open         numeric(18, 10),
  fee_close        numeric(18, 10)
);

create index if not exists backtest_trades_run_idx on public.backtest_trades (run_id, open_date);
create index if not exists backtest_trades_pair_idx on public.backtest_trades (run_id, pair);

create table if not exists public.backtest_equity_curve (
  id          bigint generated by default as identity primary key,
  run_id      uuid not null references public.backtest_runs (id) on delete cascade,
  at          timestamptz not null,
  balance     numeric(38, 18),
  drawdown_abs numeric(38, 18),
  drawdown_pct numeric(18, 8),
  constraint backtest_equity_curve_unique unique (run_id, at)
);

-- ---------------------------------------------------------------------------
-- Queue mechanics
-- ---------------------------------------------------------------------------

-- Atomically hand exactly one queued job to one worker. SKIP LOCKED means N
-- workers can drain the queue concurrently without ever taking the same job.
create or replace function public.claim_backtest_job(p_worker text)
returns public.backtest_jobs
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  job public.backtest_jobs;
begin
  select * into job
    from public.backtest_jobs
   where status = 'queued'
     and attempts < max_attempts
   order by priority asc, created_at asc
   for update skip locked
   limit 1;

  if not found then
    return null;
  end if;

  update public.backtest_jobs
     set status      = 'running',
         claimed_by  = p_worker,
         claimed_at  = now(),
         heartbeat_at = now(),
         started_at  = coalesce(started_at, now()),
         attempts    = attempts + 1
   where id = job.id
  returning * into job;

  return job;
end;
$$;

comment on function public.claim_backtest_job(text) is
  'Atomically claim the highest-priority queued backtest job. Safe for concurrent workers.';

-- Return jobs whose worker died mid-run so they can be retried.
create or replace function public.requeue_stalled_backtest_jobs(p_stale_after interval default interval '20 minutes')
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  n integer;
begin
  with revived as (
    update public.backtest_jobs
       set status = case when attempts >= max_attempts then 'failed'::public.run_status
                         else 'queued'::public.run_status end,
           error  = case when attempts >= max_attempts
                         then coalesce(error, 'worker stopped reporting; retries exhausted')
                         else error end,
           claimed_by = null,
           claimed_at = null
     where status = 'running'
       and coalesce(heartbeat_at, claimed_at, started_at) < now() - p_stale_after
    returning 1
  )
  select count(*) into n from revived;
  return n;
end;
$$;
