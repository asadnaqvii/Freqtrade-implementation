-- 0007_rls.sql
-- Row level security. Every table in `public` is exposed through PostgREST, so
-- every table gets RLS. The rule is uniform: you see your own rows.
--
-- The bot and the worker connect either as `service_role` (which has BYPASSRLS)
-- or over a direct Postgres connection as the owner, so none of these policies
-- get in their way. They exist to make the anon/authenticated API surface safe.

-- ---------------------------------------------------------------------------
-- Enable RLS everywhere
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'exchange_accounts', 'bot_instances',
    'indicator_catalog', 'strategy_specs', 'strategy_versions',
    'backtest_jobs', 'backtest_runs', 'backtest_pair_results',
    'backtest_trades', 'backtest_equity_curve',
    'validation_runs', 'validation_checks', 'order_reconciliations',
    'trade_archive', 'balance_snapshots'
  ] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('alter table public.%I force row level security', t);
  end loop;
end $$;

-- `force row level security` also applies the policies to the table owner. That
-- is deliberate: it means a mistake in the app's connection role cannot quietly
-- read everyone's data. service_role still bypasses, as it is BYPASSRLS.

-- ---------------------------------------------------------------------------
-- profiles
-- ---------------------------------------------------------------------------
drop policy if exists profiles_select_own on public.profiles;
create policy profiles_select_own on public.profiles
  for select to authenticated
  using (id = public.current_profile_id());

drop policy if exists profiles_update_own on public.profiles;
create policy profiles_update_own on public.profiles
  for update to authenticated
  using (id = public.current_profile_id())
  with check (id = public.current_profile_id());

-- ---------------------------------------------------------------------------
-- Owner-scoped tables: identical shape, generated to avoid 30 near-identical blocks
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'exchange_accounts', 'bot_instances', 'backtest_jobs',
    'backtest_runs', 'validation_runs', 'trade_archive', 'balance_snapshots'
  ] loop
    execute format('drop policy if exists %I on public.%I', t || '_rw_own', t);
    execute format($p$
      create policy %I on public.%I
        for all to authenticated
        using (owner_id = public.current_profile_id())
        with check (owner_id = public.current_profile_id())
    $p$, t || '_rw_own', t);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- strategy_specs: own rows, plus read access to shared public templates
-- ---------------------------------------------------------------------------
drop policy if exists strategy_specs_rw_own on public.strategy_specs;
create policy strategy_specs_rw_own on public.strategy_specs
  for all to authenticated
  using (owner_id = public.current_profile_id())
  with check (owner_id = public.current_profile_id());

drop policy if exists strategy_specs_read_public on public.strategy_specs;
create policy strategy_specs_read_public on public.strategy_specs
  for select to authenticated
  using (is_public and not is_archived);

-- ---------------------------------------------------------------------------
-- Child tables: reachable exactly when the parent row is
-- ---------------------------------------------------------------------------
drop policy if exists strategy_versions_rw_via_parent on public.strategy_versions;
create policy strategy_versions_rw_via_parent on public.strategy_versions
  for all to authenticated
  using (exists (
    select 1 from public.strategy_specs s
     where s.id = strategy_versions.strategy_id
       and (s.owner_id = public.current_profile_id() or s.is_public)))
  with check (exists (
    select 1 from public.strategy_specs s
     where s.id = strategy_versions.strategy_id
       and s.owner_id = public.current_profile_id()));

do $$
declare
  spec record;
begin
  for spec in
    select * from (values
      ('backtest_pair_results', 'run_id',  'backtest_runs'),
      ('backtest_trades',       'run_id',  'backtest_runs'),
      ('backtest_equity_curve', 'run_id',  'backtest_runs'),
      ('validation_checks',     'run_id',  'validation_runs'),
      ('order_reconciliations', 'run_id',  'validation_runs')
    ) as v(child, fk, parent)
  loop
    execute format('drop policy if exists %I on public.%I', spec.child || '_rw_via_parent', spec.child);
    execute format($p$
      create policy %I on public.%I
        for all to authenticated
        using (exists (select 1 from public.%I p
                        where p.id = public.%I.%I
                          and p.owner_id = public.current_profile_id()))
        with check (exists (select 1 from public.%I p
                        where p.id = public.%I.%I
                          and p.owner_id = public.current_profile_id()))
    $p$, spec.child || '_rw_via_parent', spec.child,
         spec.parent, spec.child, spec.fk,
         spec.parent, spec.child, spec.fk);
  end loop;
end $$;

-- ---------------------------------------------------------------------------
-- indicator_catalog: readable by everyone signed in, writable only by service_role
-- ---------------------------------------------------------------------------
drop policy if exists indicator_catalog_read on public.indicator_catalog;
create policy indicator_catalog_read on public.indicator_catalog
  for select to authenticated
  using (enabled);

-- ---------------------------------------------------------------------------
-- anon gets nothing. Revoke rather than rely on the absence of a policy.
-- ---------------------------------------------------------------------------
do $$
declare
  t text;
begin
  foreach t in array array[
    'profiles', 'exchange_accounts', 'bot_instances',
    'indicator_catalog', 'strategy_specs', 'strategy_versions',
    'backtest_jobs', 'backtest_runs', 'backtest_pair_results',
    'backtest_trades', 'backtest_equity_curve',
    'validation_runs', 'validation_checks', 'order_reconciliations',
    'trade_archive', 'balance_snapshots'
  ] loop
    execute format('revoke all on public.%I from anon', t);
  end loop;
end $$;

-- Bot schemas are not part of the PostgREST surface at all. Make that explicit
-- so exposing one later has to be a deliberate act.
do $$
declare
  s text;
begin
  for s in select nspname from pg_namespace where nspname like 'ft\_%' loop
    execute format('revoke all on schema %I from anon, authenticated', s);
  end loop;
end $$;
