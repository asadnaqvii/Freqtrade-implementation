-- 0022_retire_a_bot.sql
--
-- bot_instances has no way to say "this one is finished". The Railway bot was
-- shut down at cutover and its row stayed behind, still claiming
-- status='running'. Two consequences, both bad:
--
--   * v_bot_health reports it alongside the live bot, so the dashboard shows
--     two trading bots when there is one.
--   * the watchdog reads desired_state, finds null, defaults to "running", and
--     will open an `offline` incident against a machine that was deliberately
--     switched off -- paging about a non-event, which is how a person learns to
--     ignore the channel that matters.
--
-- Deleting the row is not the answer: trade_archive, strategy_deployments and
-- trade history all reference it, and that history is the record of what the
-- bot actually did. Retirement is a state, not a deletion.
--
-- health='retired' is checked before the heartbeat ages, so a retired bot never
-- reports stale or offline no matter how long ago it last checked in.

alter table public.bot_instances
  add column if not exists retired_at timestamptz;

comment on column public.bot_instances.retired_at is
  'When this bot was permanently shut down. Retired bots keep their history but '
  'are excluded from health checks and alerting.';

create or replace view public.v_bot_health
with (security_invoker = on) as
select
  b.id,
  b.owner_id,
  b.name,
  b.exchange,
  b.strategy,
  b.trading_mode,
  b.status,
  b.metadata ->> 'desired_state' as desired_state,
  b.last_heartbeat_at,
  extract(epoch from now() - b.last_heartbeat_at) as heartbeat_age_seconds,
  case
    when b.retired_at is not null then 'retired'
    when b.last_heartbeat_at is null then 'never_seen'
    when b.last_heartbeat_at > (now() - interval '5 minutes') then 'healthy'
    when b.last_heartbeat_at > (now() - interval '30 minutes') then 'stale'
    else 'offline'
  end as health,
  (select count(*) from public.trade_archive t
    where t.bot_instance_id = b.id and t.is_open) as open_trades,
  b.retired_at
from public.bot_instances b;

-- The Railway instance was shut down when trading moved to Render. Anchor the
-- retirement to the last trade it actually closed rather than to now(), so the
-- record says when it stopped working, not when this migration ran.
update public.bot_instances b
   set retired_at = coalesce(
         (select max(t.close_date) from public.trade_archive t
           where t.bot_instance_id = b.id and t.close_date is not null),
         b.last_heartbeat_at,
         now()),
       status = 'retired'
 where b.name = 'freqtrade-railway-legacy'
   and b.retired_at is null;
