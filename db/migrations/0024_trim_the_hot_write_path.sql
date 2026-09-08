-- 0024_trim_the_hot_write_path.sql
--
-- Two findings from Supabase's performance advisor, both on paths this
-- deployment actually exercises.
--
-- 1. bot_instances_heartbeat_idx has never been used -- pg_stat reports zero
--    scans since the table was created. It indexes last_heartbeat_at on a table
--    holding two rows, which no planner would ever choose a scan over, and it
--    sits on the single hottest write in the system: every heartbeat had to
--    maintain it for nothing. Dropping an index that has never served a query
--    costs nothing and takes work off the write.
--
-- 2. The owner-read policies on the tables added in 0017-0019 call auth.uid()
--    per row instead of once per query. At today's row counts this is
--    invisible; strategy_signals is the table that grows without bound, so it
--    is the one where it would eventually stop being invisible. Wrapping the
--    call in a scalar subquery makes Postgres evaluate it once -- the behaviour
--    of the policy is identical.

drop index if exists public.bot_instances_heartbeat_idx;

drop policy if exists strategy_signals_owner_read on public.strategy_signals;
create policy strategy_signals_owner_read on public.strategy_signals
  for select to authenticated
  using (owner_id = (select auth.uid()));

drop policy if exists strategy_deployments_owner_read on public.strategy_deployments;
create policy strategy_deployments_owner_read on public.strategy_deployments
  for select to authenticated
  using (owner_id = (select auth.uid()));

drop policy if exists bot_incidents_owner_read on public.bot_incidents;
create policy bot_incidents_owner_read on public.bot_incidents
  for select to authenticated
  using (owner_id = (select auth.uid()));
