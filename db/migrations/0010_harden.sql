-- 0010_harden.sql
-- Fixes raised by the Supabase security linter after 0001-0008.
--
-- The one that actually mattered: claim_backtest_job() and
-- requeue_stalled_backtest_jobs() are SECURITY DEFINER, and Postgres grants
-- EXECUTE to PUBLIC by default. PostgREST turns that into
-- /rest/v1/rpc/claim_backtest_job callable with nothing but the anon key --
-- so anyone could drain the queue or requeue other people's jobs. These are
-- worker-only entry points; only service_role has any business calling them.

-- ---------------------------------------------------------------------------
-- 1. Worker-only functions: revoke from the API roles
-- ---------------------------------------------------------------------------
revoke all on function public.claim_backtest_job(text) from public, anon, authenticated;
revoke all on function public.requeue_stalled_backtest_jobs(interval) from public, anon, authenticated;
revoke all on function public.refresh_freqtrade_views(text) from public, anon, authenticated;
grant execute on function public.claim_backtest_job(text) to service_role;
grant execute on function public.requeue_stalled_backtest_jobs(interval) to service_role;
grant execute on function public.refresh_freqtrade_views(text) to service_role;

-- Trigger functions are never meant to be called over RPC.
revoke all on function public.handle_new_auth_user() from public, anon, authenticated;
revoke all on function public.set_updated_at() from public, anon, authenticated;
revoke all on function public.strategy_versions_immutable() from public, anon, authenticated;

-- next_strategy_version is called by the app on behalf of a signed-in user.
revoke all on function public.next_strategy_version(uuid) from public, anon;
grant execute on function public.next_strategy_version(uuid) to authenticated, service_role;

-- current_profile_id just returns auth.uid(); harmless for a signed-in caller,
-- pointless for anon.
revoke all on function public.current_profile_id() from public, anon;
grant execute on function public.current_profile_id() to authenticated, service_role;

-- Pre-existing helper from the earlier effort, same treatment.
do $$
begin
  if to_regprocedure('public.rls_auto_enable()') is not null then
    revoke all on function public.rls_auto_enable() from public, anon, authenticated;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 2. Pin search_path on every function we own
-- ---------------------------------------------------------------------------
-- Without this a caller can point search_path at a schema they control and
-- shadow the tables these functions reference.
alter function public.set_updated_at()               set search_path = public, pg_temp;
alter function public.strategy_versions_immutable()  set search_path = public, pg_temp;
alter function public.next_strategy_version(uuid)    set search_path = public, pg_temp;

do $$
begin
  if to_regprocedure('public.update_updated_at()') is not null then
    alter function public.update_updated_at() set search_path = public, pg_temp;
  end if;
end $$;

-- ---------------------------------------------------------------------------
-- 3. Views: anon should not see them at all
-- ---------------------------------------------------------------------------
-- 0007 revoked the tables from anon but not the views built on top of them.
do $$
declare
  v text;
begin
  foreach v in array array[
    'v_strategy_performance', 'v_bot_health',
    'v_account_verification', 'v_trade_pnl_daily'
  ] loop
    if to_regclass('public.' || v) is not null then
      execute format('revoke all on public.%I from anon', v);
      execute format('grant select on public.%I to authenticated', v);
    end if;
  end loop;
end $$;

-- v_live_trades / v_live_orders are created later by refresh_freqtrade_views().
-- That function grants them itself; see 0011.
