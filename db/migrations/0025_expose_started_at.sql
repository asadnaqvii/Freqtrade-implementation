-- 0025_expose_started_at.sql
--
-- The watchdog opened a `not_trading` incident at 06:28:45 on 2026-09-05 --
-- "heartbeating normally but reporting unreachable" -- and resolved it 61
-- seconds later. Nothing was wrong. A rolling deploy had just replaced the bot,
-- and the new instance registers and starts heartbeating before its local API
-- is serving, so for one sweep it looks alive but unreachable.
--
-- `not_trading` is in NOTIFY_KINDS, so with a webhook configured that would
-- have paged on every deploy. The watchdog's own docstring says a watchdog that
-- cries during every deploy gets muted, and a muted watchdog is worse than
-- none; this is that, so it needs the one fact that separates the two cases:
-- how long the process has been up.
--
-- started_at is already on bot_instances. The view just never exposed it.

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
  b.retired_at,
  b.started_at,
  extract(epoch from now() - b.started_at) as uptime_seconds
from public.bot_instances b;
