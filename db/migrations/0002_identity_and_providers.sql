-- 0002_identity_and_providers.sql
-- Who the user is, which bots they run, and which wallets/exchanges they
-- connected. Credentials are NEVER stored here -- only the name of the secret
-- and a fingerprint good enough to tell two keys apart.

-- ---------------------------------------------------------------------------
-- profiles: one row per authenticated user
-- ---------------------------------------------------------------------------
create table if not exists public.profiles (
  id            uuid primary key,
  email         text,
  display_name  text,
  role          text not null default 'member'
                  check (role in ('member', 'admin', 'service')),
  -- Guard rails so one account cannot queue unbounded backtests.
  max_concurrent_backtests int not null default 2 check (max_concurrent_backtests between 0 and 20),
  max_strategies           int not null default 50 check (max_strategies between 0 and 1000),
  settings      jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

comment on table public.profiles is
  'Application-level user record. id matches auth.users.id when Supabase Auth is used.';

-- Link to auth.users only if that schema is present (it always is on Supabase,
-- but this keeps the migration runnable against a bare Postgres).
do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'auth' and table_name = 'users')
     and not exists (select 1 from information_schema.table_constraints
                     where constraint_name = 'profiles_id_fkey'
                       and table_schema = 'public') then
    alter table public.profiles
      add constraint profiles_id_fkey
      foreign key (id) references auth.users (id) on delete cascade;
  end if;
end $$;

drop trigger if exists profiles_set_updated_at on public.profiles;
create trigger profiles_set_updated_at
  before update on public.profiles
  for each row execute function public.set_updated_at();

-- Auto-create a profile whenever a new auth user signs up.
create or replace function public.handle_new_auth_user()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  insert into public.profiles (id, email, display_name)
  values (new.id, new.email, coalesce(new.raw_user_meta_data ->> 'full_name', split_part(new.email, '@', 1)))
  on conflict (id) do nothing;
  return new;
end;
$$;

do $$
begin
  if exists (select 1 from information_schema.tables
             where table_schema = 'auth' and table_name = 'users') then
    drop trigger if exists on_auth_user_created on auth.users;
    create trigger on_auth_user_created
      after insert on auth.users
      for each row execute function public.handle_new_auth_user();
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- exchange_accounts: a connected wallet / exchange provider
-- ---------------------------------------------------------------------------
create table if not exists public.exchange_accounts (
  id                uuid primary key default gen_random_uuid(),
  owner_id          uuid references public.profiles (id) on delete cascade,
  label             text not null,
  provider          text not null,                       -- 'kucoin', 'binance', 'paper', ...
  provider_kind     public.provider_kind not null default 'exchange',
  ccxt_id           text,                                -- ccxt exchange id when provider_kind = 'exchange'
  is_sandbox        boolean not null default false,
  is_active         boolean not null default true,

  -- Secret handling. The platform reads the actual key from the process
  -- environment (or a secret manager) using these variable names. Storing the
  -- name -- not the value -- means a database leak is not a funds leak.
  api_key_env_var       text,
  api_secret_env_var    text,
  api_password_env_var  text,
  -- sha256 of the API key, truncated. Lets the UI say "this is the same key you
  -- verified on Tuesday" without ever holding the key.
  api_key_fingerprint   text,

  -- Filled in by the verification run.
  permissions           text[] not null default '{}',
  account_ref           text,
  last_verified_at      timestamptz,
  last_verification     public.check_status,
  verification_notes    text,

  metadata          jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint exchange_accounts_label_unique unique (owner_id, label),
  -- A real exchange connection needs a ccxt driver to talk to; paper accounts do not.
  constraint exchange_accounts_ccxt_required
    check (provider_kind <> 'exchange' or ccxt_id is not null)
);

create index if not exists exchange_accounts_owner_idx on public.exchange_accounts (owner_id);
create index if not exists exchange_accounts_provider_idx on public.exchange_accounts (provider) where is_active;

drop trigger if exists exchange_accounts_set_updated_at on public.exchange_accounts;
create trigger exchange_accounts_set_updated_at
  before update on public.exchange_accounts
  for each row execute function public.set_updated_at();

comment on column public.exchange_accounts.api_key_fingerprint is
  'sha256 hex of the API key. Identifies a key across rotations; cannot be reversed into one.';

-- ---------------------------------------------------------------------------
-- bot_instances: a deployed freqtrade process
-- ---------------------------------------------------------------------------
create table if not exists public.bot_instances (
  id                uuid primary key default gen_random_uuid(),
  owner_id          uuid references public.profiles (id) on delete cascade,
  account_id        uuid references public.exchange_accounts (id) on delete set null,
  name              text not null,
  environment       text not null default 'production',
  exchange          text not null,
  strategy          text,
  trading_mode      public.trading_mode not null default 'dry_run',
  stake_currency    text not null default 'USDT',
  stake_amount      numeric(38, 18),
  max_open_trades   integer,
  freqtrade_version text,
  deploy_target     text,                                -- 'render', 'docker', 'local'
  api_base_url      text,                                -- private-network URL, e.g. http://freqtrade-bot:8080
  config_fingerprint text,
  status            text not null default 'unknown',
  started_at        timestamptz,
  last_heartbeat_at timestamptz,
  metadata          jsonb not null default '{}'::jsonb,
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now(),

  constraint bot_instances_name_unique unique (owner_id, name)
);

create index if not exists bot_instances_owner_idx on public.bot_instances (owner_id);
create index if not exists bot_instances_heartbeat_idx on public.bot_instances (last_heartbeat_at desc);

drop trigger if exists bot_instances_set_updated_at on public.bot_instances;
create trigger bot_instances_set_updated_at
  before update on public.bot_instances
  for each row execute function public.set_updated_at();

comment on column public.bot_instances.api_base_url is
  'Where the freqtrade REST API answers. On Render this is the private-network host, unreachable from the internet.';
