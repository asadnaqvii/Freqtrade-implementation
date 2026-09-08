-- 0001_foundation.sql
-- Schemas, enums and shared helpers for the Freqtrade trading platform.
--
-- Schema layout:
--   ft_<bot>   -> owned by freqtrade itself (trades/orders/pairlocks/keyvaluestore),
--                 one schema per bot instance, created in 0006. Deliberately NOT
--                 exposed through PostgREST: the bot connects over raw Postgres,
--                 and nothing in the app writes there.
--   public     -> the application schema. Exposed through PostgREST, every table
--                 is RLS-protected (see 0007_rls.sql).

-- Bot schemas are created in 0006 (ft_main), named per bot instance.
-- create schema if not exists freqtrade;   -- superseded

-- comment on schema freqtrade is
--   'superseded by ft_main in 0006';

-- gen_random_uuid() lives in pgcrypto on older engines; on PG13+ it is built in.
create extension if not exists pgcrypto with schema extensions;

-- ---------------------------------------------------------------------------
-- Enums
-- ---------------------------------------------------------------------------

do $$ begin
  create type public.trading_mode as enum ('dry_run', 'live', 'backtest');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.provider_kind as enum ('exchange', 'custodial_wallet', 'self_custody', 'paper');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.check_status as enum ('passed', 'warning', 'failed', 'skipped', 'error');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.check_severity as enum ('info', 'warning', 'critical');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.run_status as enum ('queued', 'running', 'completed', 'failed', 'cancelled');
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.validation_kind as enum (
    'connectivity',      -- can we reach the provider and are the credentials valid
    'preflight',         -- is this specific order placeable right now
    'reconciliation',    -- do freqtrade's records match the exchange's records
    'balance',           -- does the wallet hold what we think it holds
    'strategy'           -- does a generated strategy compile and behave
  );
exception when duplicate_object then null; end $$;

do $$ begin
  create type public.strategy_source as enum ('builder', 'uploaded', 'builtin');
exception when duplicate_object then null; end $$;

-- ---------------------------------------------------------------------------
-- Shared helpers
-- ---------------------------------------------------------------------------

create or replace function public.set_updated_at()
returns trigger
language plpgsql
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function public.set_updated_at() is
  'Trigger helper: stamps updated_at on every UPDATE.';

-- Returns the auth uid, or null when running as a service role / raw postgres
-- connection. Wrapped so the app schema does not depend on the auth schema
-- existing (useful when restoring into a plain Postgres for local dev).
create or replace function public.current_profile_id()
returns uuid
language plpgsql
stable
security definer
set search_path = public, auth, pg_temp
as $$
declare
  uid uuid;
begin
  begin
    uid := auth.uid();
  exception when others then
    uid := null;
  end;
  return uid;
end;
$$;
