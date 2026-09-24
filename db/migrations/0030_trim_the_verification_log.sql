-- 0030_trim_the_verification_log.sql
--
-- The verification log was rewriting the same answer forever.
--
-- Reconciliation re-checks every order the bot knows about on every run, and
-- for an order that closed weeks ago the answer never changes again. It ran
-- hourly and wrote a fresh verdict row for every order each time, and nothing
-- ever deleted one. On production that turned 142 real orders into 46,935
-- rows and 11 MB -- 330 identical verdicts per order -- on the smallest
-- database Supabase sells, which is what finally exhausted it: the trading
-- bot spent 2026-09-20 unable to reach its own database while this table
-- absorbed the write budget.
--
-- Two fixes here, and a third in the code: the writer now skips a verdict
-- that repeats the one already on record (app/validation/engine.py), so in
-- the steady state nothing is written at all.
--
--   1. prune_validation_records() gives the log a retention window. Deleting
--      a validation_run cascades to its checks and its reconciliations, so
--      one bounded delete trims all three tables. Capped per call, like
--      prune_security_events (0020), so a backlog is worked off in chunks
--      rather than one long lock.
--
--   2. A one-off cleanup of what has already piled up. It keeps the first row
--      of every run of identical verdicts, so the history of what *changed*
--      survives intact and only the repeats go. Idempotent: running it again
--      finds nothing left to remove.

create or replace function public.prune_validation_records(
  p_keep_days int default 90,
  p_limit     int default 5000
)
returns int
language sql
security definer
set search_path = public, pg_temp
as $$
  with doomed as (
    select id from public.validation_runs
    where started_at < now() - make_interval(days => greatest(p_keep_days, 1))
    order by started_at
    limit greatest(p_limit, 1)
  ), removed as (
    delete from public.validation_runs r
    using doomed d where r.id = d.id
    returning 1
  )
  -- A real count, not the `returning 1` of prune_security_events (0020), which
  -- hands back 1 however many rows it deleted and null when it deleted none.
  -- The worker logs this number, so it should be the number.
  select coalesce(count(*), 0)::int from removed;
$$;

comment on function public.prune_validation_records(int, int) is
  'Retention for the verification log. Deletes validation_runs past the window; validation_checks and order_reconciliations cascade with them.';

revoke all on function public.prune_validation_records(int, int) from public, anon, authenticated;
grant execute on function public.prune_validation_records(int, int) to service_role;

-- The one-off cleanup. Keeps the first row of each run of identical verdicts
-- for an order, drops the repeats that follow it.
with ranked as (
  select
    id,
    row_number() over w                as rn,
    lag(matched)          over w       as prev_matched,
    lag(discrepancy_kind) over w       as prev_kind,
    lag(notes)            over w       as prev_notes,
    matched, discrepancy_kind, notes
  from public.order_reconciliations
  window w as (
    partition by bot_instance_id, pair,
                 coalesce(ft_order_id, ''), coalesce(exchange_order_id, '')
    order by checked_at, id
  )
)
delete from public.order_reconciliations o
using ranked r
where o.id = r.id
  and r.rn > 1
  and r.matched          is not distinct from r.prev_matched
  and r.discrepancy_kind is not distinct from r.prev_kind
  and r.notes            is not distinct from r.prev_notes;
