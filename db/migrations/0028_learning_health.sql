-- 0028_learning_health.sql
--
-- How the Learning Module says whether it is working.
--
-- The writer inside the bot publishes one row per bot, once a minute: what
-- is queued locally, how old the oldest queued record is, how many have been
-- quarantined, how many written. That is the writer's own account of itself.
-- v_learning_health joins it to what actually arrived in the evidence tables
-- in the last day, so "the writer says it is fine" and "decisions are
-- arriving" are shown together -- a pipeline that died quietly shows as a
-- fresh status row next to a stale last_decision_at.
--
-- Its own table rather than more keys in bot_instances.metadata: that row is
-- on the hot heartbeat path 0024 trimmed, and this one changes every minute.

create table if not exists public.learning_writer_status (
  bot_instance_id            uuid primary key references public.bot_instances(id) on delete cascade,
  owner_id                   uuid references public.profiles(id) on delete cascade,
  reported_at                timestamptz not null default now(),
  enabled                    boolean not null default true,
  adapter_version            text,
  outbox_pending             integer not null default 0,
  outbox_oldest_age_seconds  integer,
  outbox_quarantined         integer not null default 0,
  written_total              bigint not null default 0,
  failed_total               bigint not null default 0,
  retried_total              bigint not null default 0,
  dropped_total              bigint not null default 0,
  outages_total              bigint not null default 0,
  decisions_opened           bigint not null default 0,
  decisions_deduped          bigint not null default 0,
  rejections                 jsonb not null default '{}'::jsonb,
  last_success_at            timestamptz,
  last_error                 text
);

comment on table public.learning_writer_status is
  'The Learning Module writer''s own health, one row per bot, refreshed every minute by the bot.';

alter table public.learning_writer_status enable row level security;
alter table public.learning_writer_status force row level security;

drop policy if exists learning_writer_status_owner_read on public.learning_writer_status;
create policy learning_writer_status_owner_read on public.learning_writer_status
  for select to authenticated
  using (owner_id = (select auth.uid()));

revoke all on public.learning_writer_status from anon;
revoke insert, update, delete, truncate, references, trigger on public.learning_writer_status from authenticated;
grant select on public.learning_writer_status to authenticated;
grant select, insert, update, delete on public.learning_writer_status to service_role;

create or replace view public.v_learning_health
with (security_invoker = on) as
select
  s.bot_instance_id,
  s.owner_id,
  s.reported_at,
  s.enabled,
  s.adapter_version,
  s.outbox_pending,
  s.outbox_oldest_age_seconds,
  s.outbox_quarantined,
  s.written_total,
  s.failed_total,
  s.retried_total,
  s.dropped_total,
  s.outages_total,
  s.decisions_opened,
  s.decisions_deduped,
  s.rejections,
  s.last_success_at,
  s.last_error,
  (select count(*) from public.trading_decisions d
    where d.bot_instance_id = s.bot_instance_id
      and d.decision_time_utc > now() - interval '24 hours') as decisions_24h,
  (select count(*) from public.trading_decisions d
    where d.bot_instance_id = s.bot_instance_id
      and d.quarantined
      and d.decision_time_utc > now() - interval '24 hours') as quarantined_24h,
  (select count(*) from public.trading_events e
    where e.bot_instance_id = s.bot_instance_id
      and e.event_time_utc > now() - interval '24 hours') as events_24h,
  (select count(*) from public.trading_events e
    where e.bot_instance_id = s.bot_instance_id
      and e.decision_id is null
      and e.event_type not in ('bot_status', 'unlinked_position_observed', 'unaccounted_exchange_activity')
      and e.event_time_utc > now() - interval '24 hours') as events_without_decision_24h,
  (select count(*) from public.trading_decisions d
    where d.bot_instance_id = s.bot_instance_id
      and d.decision_time_utc > now() - interval '24 hours'
      and not exists (select 1 from public.trading_events e where e.decision_id = d.decision_id)) as decisions_without_events_24h,
  (select max(d.decision_time_utc) from public.trading_decisions d
    where d.bot_instance_id = s.bot_instance_id) as last_decision_at
from public.learning_writer_status s;

grant select on public.v_learning_health to authenticated, service_role;
