-- 0023_housekeeping_inside_metadata.sql
--
-- 0020 stopped the audit trigger recording heartbeats by comparing the row with
-- the housekeeping columns removed. It left one behind: bot_instances.metadata
-- carries `verify_ran_at`, a timestamp the self-check stamps every fifteen
-- minutes. The column genuinely changes, so the comparison saw a real edit and
-- wrote a full before+after snapshot of the row -- about 96 a day, each around
-- 2 KB, for nothing.
--
-- metadata also holds desired_state, which is the Start/Stop button and must
-- stay audited. So the noise is stripped key by key rather than the whole
-- column being ignored.

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
  -- Columns whose only job is to record that a periodic task ran.
  noise    text[] := array[
    'updated_at',
    'last_heartbeat_at',
    'last_verified_at',
    'last_verification'
  ];
  -- The same, for keys nested inside the metadata document.
  meta_noise text[] := array[
    'verify_ran_at',
    'selfcheck_ran_at',
    'last_seen_at'
  ];
begin
  if tg_op = 'INSERT' then
    ev := tg_argv[0] || '.created';
    after_j := to_jsonb(new);
    owner := new.owner_id;
  elsif tg_op = 'UPDATE' then
    before_j := to_jsonb(old);
    after_j := to_jsonb(new);

    if (
      case when before_j ? 'metadata'
           then jsonb_set(before_j - noise, '{metadata}',
                          coalesce(before_j -> 'metadata', '{}'::jsonb) - meta_noise)
           else before_j - noise end
    ) = (
      case when after_j ? 'metadata'
           then jsonb_set(after_j - noise, '{metadata}',
                          coalesce(after_j -> 'metadata', '{}'::jsonb) - meta_noise)
           else after_j - noise end
    ) then
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
