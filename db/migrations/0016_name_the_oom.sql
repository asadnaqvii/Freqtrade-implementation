-- Say "out of memory" when that is what happened.
--
-- A backtest that exceeds the worker's memory limit does not raise, log, or
-- fail. The kernel stops the process, the platform restarts the service, the
-- stall sweep finds a job whose heartbeat went quiet and requeues it, and the
-- whole thing happens again -- three times, and then the job is marked failed
-- with "worker stopped reporting; retries exhausted".
--
-- Every word of that is true and none of it is useful. Nothing in the chain
-- ever mentions memory, because nothing in the chain ever gets to write an
-- error: the process is killed outright. Observed 2026-08-20, worker OOM-killed
-- at 512Mi twice on one two-year 5m backtest.
--
-- Idempotent; safe to re-run.

create or replace function public.requeue_stalled_backtest_jobs(p_stale_after interval default '20 minutes')
returns integer
language plpgsql
security definer
set search_path = public
as $fn$
declare
  n integer;
begin
  with revived as (
    update public.backtest_jobs
       set status = case when attempts >= max_attempts then 'failed'::public.run_status
                         else 'queued'::public.run_status end,
           error  = case when attempts >= max_attempts
                         then coalesce(
                           error,
                           'Cut short ' || attempts || ' times with no error recorded. '
                           || 'That is what an out-of-memory kill looks like: the worker is '
                           || 'stopped before it can report anything. Try a shorter window, '
                           || 'fewer pairs, or a coarser timeframe -- or give the worker more '
                           || 'memory.')
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
$fn$;
