-- 0009_secure_legacy_tables.sql
--
-- NOT APPLIED AUTOMATICALLY -- review before running.
--
-- This project's Postgres carries five tables from an earlier WhatsApp trading
-- assistant: users, strategies, macro_events, conversations, trade_logs. All
-- five are empty and all five have RLS disabled, which means anyone holding the
-- project's anon key can read and write every row in them.
--
-- Three of them have a user_id column, so they take the same owner-scoped policy
-- as the rest of the platform. public.users is keyed by its own id. macro_events
-- has no owner at all -- it is global scan output -- so it becomes read-only to
-- signed-in users and writable only by service_role.
--
-- If you would rather delete them, the drop is at the bottom, commented out.
-- They are empty, so nothing is lost either way; the choice is whether anything
-- outside this repo still points at them.

alter table public.users        enable row level security;
alter table public.strategies   enable row level security;
alter table public.macro_events enable row level security;
alter table public.conversations enable row level security;
alter table public.trade_logs   enable row level security;

-- users: a row is yours if it is you.
drop policy if exists legacy_users_rw_own on public.users;
create policy legacy_users_rw_own on public.users
  for all to authenticated
  using (id = public.current_profile_id())
  with check (id = public.current_profile_id());

-- The three tables that carry user_id.
do $$
declare
  t text;
begin
  foreach t in array array['strategies', 'conversations', 'trade_logs'] loop
    execute format('drop policy if exists %I on public.%I', 'legacy_' || t || '_rw_own', t);
    execute format($p$
      create policy %I on public.%I
        for all to authenticated
        using (user_id = public.current_profile_id())
        with check (user_id = public.current_profile_id())
    $p$, 'legacy_' || t || '_rw_own', t);
  end loop;
end $$;

-- macro_events is global scan output with no owner column. Signed-in users may
-- read it; only service_role writes.
drop policy if exists legacy_macro_events_read on public.macro_events;
create policy legacy_macro_events_read on public.macro_events
  for select to authenticated
  using (true);

revoke all on public.users, public.strategies, public.macro_events,
              public.conversations, public.trade_logs
  from anon;

-- ---------------------------------------------------------------------------
-- Alternative: drop them instead. Uncomment only if nothing else uses them.
-- ---------------------------------------------------------------------------
-- drop table if exists public.conversations;
-- drop table if exists public.trade_logs;
-- drop table if exists public.macro_events;
-- drop table if exists public.strategies;
-- drop table if exists public.users;
