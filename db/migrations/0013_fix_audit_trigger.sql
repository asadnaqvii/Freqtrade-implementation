-- 0013_fix_audit_trigger.sql
-- audit_security_event() from 0012 is attached to both exchange_accounts and
-- bot_instances. Its key-rotation branch referenced new.api_key_fingerprint,
-- guarded by `tg_op = 'UPDATE' and tg_argv[0] = 'account'`, on the assumption
-- that plpgsql would short-circuit before touching a field bot_instances does
-- not have.
--
-- It does not short-circuit. plpgsql compiles the whole boolean expression into
-- a single SQL statement, so the field reference is resolved regardless of the
-- guard, and every write to bot_instances failed with:
--
--   ERROR: record "new" has no field "api_key_fingerprint"
--
-- The bot registers itself in bot_instances on every boot, so this would have
-- broken bot startup. Found by inserting the migrated Railway history.
--
-- Fix: compare through the jsonb snapshots the function already builds, so the
-- expression never names a column that may not exist on the table it fires for.

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
begin
  if tg_op = 'INSERT' then
    ev := tg_argv[0] || '.created';
    after_j := to_jsonb(new);
    owner := new.owner_id;
  elsif tg_op = 'UPDATE' then
    ev := tg_argv[0] || '.updated';
    before_j := to_jsonb(old);
    after_j := to_jsonb(new);
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
