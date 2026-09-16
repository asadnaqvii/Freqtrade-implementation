-- 0026_incident_paging.sql
--
-- Two things the five-day outage of 11-16 September showed the watchdog
-- lacked.
--
-- First, it paged once, at minute one, and never again. An alert that arrives
-- while an outage still looks like a blip is the one that gets skimmed; the
-- same incident still open half an hour later is news, and the watchdog now
-- says so again. That needs a record of when it last paged.
--
-- Second, "stopped" and "stopped with four open positions and no stop-loss
-- being managed" were the same word on every dashboard. Being stopped is the
-- user's choice and pages nobody; being stopped with positions is recorded as
-- its own kind, so the dashboard can show it for what it is.
--
-- The enum value is added on its own: Postgres will not let a transaction use
-- a value it added, so nothing here uses it.

alter type public.incident_kind add value if not exists 'stopped_with_positions';

alter table public.bot_incidents
  add column if not exists last_paged_at timestamptz,
  add column if not exists pages integer not null default 0;

comment on column public.bot_incidents.last_paged_at is
  'When a person was last told about this incident. Repeated every 30 minutes while it stays open.';
comment on column public.bot_incidents.pages is
  'How many times a person has been told.';
