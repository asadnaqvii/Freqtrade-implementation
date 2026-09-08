-- 0020_stop_auditing_the_clock.sql
--
-- audit_security_event() records a full before+after jsonb snapshot of the row
-- on every UPDATE. That is right for a key rotation and absurd for a heartbeat.
--
-- The bot stamps last_heartbeat_at every 60s and the self-check stamps
-- last_verified_at every 15m. Neither changes anything a human would want
-- audited, and both fired the trigger. As of 2026-09-04:
--
--   bot.updated       24,699 rows   50 MB    -- all heartbeats
--   account.updated    3,606 rows   5.5 MB   -- all verification stamps
--
-- security_events had grown to 70 MB of a 100 MB database, and bot_instances --
-- two rows -- had been autovacuumed 456 times and autoanalyzed 483 times.
--
-- The cost is not the storage. This project runs archive_mode=on with
-- archive_timeout=120s over 16 MB WAL segments, so any write activity inside a
-- two-minute window forces a full segment switch. Measured data change rate was
-- ~290 bytes/s; measured WAL was ~8 MB/min (~11.5 GB/day). A trickle of writes
-- that never stops costs the same disk IO as a firehose, and Supabase warned
-- that the project was depleting its Disk IO budget.
--
-- Fix: compare the snapshots with the housekeeping columns removed. A status
-- change, a key rotation, a schema change -- all still audited. A clock tick is
-- not an event.

create or replace function public.audit_security_event()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  ev       text;
  owner    uuid;
  before_j jsonb;
  after_j  jsonb;
  -- Columns whose only job is to record that a periodic task ran. A write that
  -- touches nothing else is housekeeping, not a change worth keeping.
  noise    text[] := array[
    'updated_at',
    'last_heartbeat_at',
    'last_verified_at',
    'last_verification'
  ];
begin
  if tg_op = 'INSERT' then
    ev := tg_argv[0] || '.created';
    after_j := to_jsonb(new);
    owner := new.owner_id;
  elsif tg_op = 'UPDATE' then
    before_j := to_jsonb(old);
    after_j := to_jsonb(new);

    -- Nothing of substance moved: leave without writing a row.
    if (before_j - noise) = (after_j - noise) then
      return new;
    end if;

    ev := tg_argv[0] || '.updated';
    owner := new.owner_id;

    if tg_argv[0] = 'account'
       and (before_j -> 'api_key_fingerprint' is distinct from after_j -> 'api_key_fingerprint'
            or before_j -> 'api_key_env_var' is distinct from after_j -> 'api_key_env_var') then
      ev := 'account.key_rotated';
    end if;
  else
    ev := tg_argv[0] || '.deleted';
    before_j := to_jsonb(old);
    owner := old.owner_id;
  end if;

  insert into public.security_events (owner_id, actor, event, entity, entity_id, before, after)
  values (owner, current_user, ev, tg_table_name,
          coalesce(new.id, old.id)::text, before_j, after_j);

  return coalesce(new, old);
end;
$$;

revoke all on function public.audit_security_event() from public, anon, authenticated;

-- Retention. Without this the table only ever grows, and a security log nobody
-- can page through is not a security log. Deletes are capped per call so a
-- backlog is worked off in bounded chunks rather than one long lock.
create or replace function public.prune_security_events(
  p_keep_days int default 90,
  p_limit     int default 20000
)
returns int
language sql
security definer
set search_path = public, pg_temp
as $$
  with doomed as (
    select id from public.security_events
    where at < now() - make_interval(days => greatest(p_keep_days, 1))
    order by at
    limit greatest(p_limit, 1)
  )
  delete from public.security_events e
  using doomed d where e.id = d.id
  returning 1;
$$;

revoke all on function public.prune_security_events(int, int) from public, anon, authenticated;

-- Clear out what the old trigger already wrote. These rows record that a
-- timestamp advanced and nothing else; the comparison below is the same one
-- the trigger now makes before writing.
delete from public.security_events
where event in ('bot.updated', 'account.updated')
  and before is not null
  and after is not null
  and (before - array['updated_at','last_heartbeat_at','last_verified_at','last_verification'])
    = (after  - array['updated_at','last_heartbeat_at','last_verified_at','last_verification']);
