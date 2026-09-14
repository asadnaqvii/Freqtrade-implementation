-- Let one person sign in with more than one email address.
--
-- profiles.id is the Supabase auth uid, and every RLS policy compares owner_id
-- against current_profile_id(). So a second email is a second profile that owns
-- nothing, and signing in with it shows an empty dashboard.
--
-- 21 of the 24 policies already route through current_profile_id(); the other
-- three are deliberate public reads (public strategies, the indicator catalogue,
-- a legacy table). That makes the function the one place to express "this login
-- acts as that owner", instead of editing 21 policies.
--
-- The security property that matters: you may grant access to YOUR data, and you
-- may never claim someone else's. Two things enforce it.
--
--   1. The write policies below compare against auth.uid() directly, NOT
--      current_profile_id(). Using the helper would let a delegate add further
--      delegates in the owner's name -- an escalation chain from one grant.
--   2. Resolution is a single hop. If A grants B, and B grants C, then C resolves
--      to B, not to A. A chain cannot walk up to the original owner.

create table if not exists public.profile_delegates (
  -- One login acts as at most one owner, so the delegate is the primary key.
  delegate_id uuid primary key references public.profiles (id) on delete cascade,
  owner_id    uuid not null references public.profiles (id) on delete cascade,
  label       text,
  created_at  timestamptz not null default now(),

  constraint profile_delegates_no_self check (delegate_id <> owner_id)
);

create index if not exists profile_delegates_owner_idx
  on public.profile_delegates (owner_id);

comment on table public.profile_delegates is
  'Extra logins that act as an existing owner. Resolved by current_profile_id(); '
  'grants are made by the owner only, and resolution never chains.';

alter table public.profile_delegates enable row level security;
alter table public.profile_delegates force row level security;

drop policy if exists profile_delegates_owner_manage on public.profile_delegates;
create policy profile_delegates_owner_manage on public.profile_delegates
  for all to authenticated
  -- auth.uid(), deliberately: see note 1 above.
  using (owner_id = auth.uid())
  with check (owner_id = auth.uid());

drop policy if exists profile_delegates_see_own_grant on public.profile_delegates;
create policy profile_delegates_see_own_grant on public.profile_delegates
  for select to authenticated
  using (delegate_id = auth.uid());

-- ---------------------------------------------------------------------------
-- The one-hop resolution
-- ---------------------------------------------------------------------------
create or replace function public.current_profile_id()
returns uuid
language plpgsql
stable
security invoker
set search_path to 'public', 'auth', 'pg_temp'
as $function$
declare
  uid       uuid;
  acting_as uuid;
begin
  begin
    uid := auth.uid();
  exception when others then
    uid := null;
  end;

  if uid is null then
    return null;
  end if;

  -- One lookup, never a loop: a grant cannot be chained into someone else's data.
  -- This reads profile_delegates under the caller's own privileges, and that
  -- table's policies use auth.uid() rather than this function, so there is no
  -- recursion back into here.
  select d.owner_id into acting_as
    from public.profile_delegates d
   where d.delegate_id = uid;

  return coalesce(acting_as, uid);
end;
$function$;

comment on function public.current_profile_id() is
  'The profile whose data this request may touch: the caller, or the owner they '
  'have been granted access to. Single hop, never chained.';
