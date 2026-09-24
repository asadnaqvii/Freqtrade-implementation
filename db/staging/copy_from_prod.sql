-- copy_from_prod.sql
--
-- Turn an empty Supabase project into a copy of production: schema, data and
-- auth users. Runs entirely inside the new project's Postgres over dblink and
-- postgres_fdw, so nothing leaves Supabase and no dump file is needed (the
-- project's direct host is IPv6-only and Render, and most laptops, cannot
-- reach it; the pooler can). Apply each block through the SQL editor or the
-- MCP apply_migration tool, as the postgres role, in order. Every block is
-- idempotent.
--
-- Fill in the three placeholders. The password is production's database
-- password: rotate it when the copy is done, and redact it from this
-- project's supabase_migrations history (block D does that).
--
--   <PROD_POOLER_HOST>  e.g. aws-1-ap-northeast-1.pooler.supabase.com
--   <PROD_REF>          production's project ref
--   <PROD_PASSWORD>     production's database password (role postgres)

-- ── A. extensions ──────────────────────────────────────────────────────────
create extension if not exists postgres_fdw with schema extensions;
create extension if not exists dblink with schema extensions;

-- ── B. schema: replay production's migration history, then take its views
--       and functions as they are now ─────────────────────────────────────
--
-- Production's supabase_migrations table holds every migration exactly as it
-- was applied, including hand fixes that never became files. Two entries are
-- skipped: one belongs to an earlier project whose tables do not exist here,
-- the other secures those same absent tables. A migration that redefines a
-- view with columns in a new order is refused by "create or replace", so any
-- view a migration redefines is dropped first. freqtrade's own tables
-- (ft_main) are created from production's catalog rather than left for
-- freqtrade's first boot, because the views reference them and the data copy
-- needs them. Then every view and function is recreated from production's
-- current definition, with its grants.
do $$
declare
  r record;
  m text[];
  g text;
  conn constant text := 'host=<PROD_POOLER_HOST> port=5432 dbname=postgres user=postgres.<PROD_REF> password=<PROD_PASSWORD> sslmode=require connect_timeout=15';
  q constant text := $q$ select version, name, statements from supabase_migrations.schema_migrations
                          where name not in ('add_per_user_trading_columns', 'secure_legacy_tables')
                          order by version $q$;
begin
  for r in select * from extensions.dblink(conn, q) as t(version text, name text, statements text[]) loop
    for m in select regexp_matches(r.statements[1], 'create\s+or\s+replace\s+view\s+(?:public\.)?([a-z0-9_]+)', 'gi') loop
      execute format('drop view if exists public.%I', m[1]);
    end loop;
    execute r.statements[1];
  end loop;
  insert into supabase_migrations.schema_migrations (version, name, statements)
    select * from extensions.dblink(conn, q) as t(version text, name text, statements text[])
  on conflict do nothing;

  -- the role freqtrade connects as: give it a NEW password, never production's
  if not exists (select 1 from pg_roles where rolname = 'ft_bot') then
    create role ft_bot login;
  end if;
  alter role ft_bot set search_path = ft_main, public;
  grant ft_bot to postgres;

  -- freqtrade's enum types, then its tables, constraints, indexes and sequences,
  -- rebuilt from production's catalog
  for r in select * from extensions.dblink(conn,
      $q$ select t.typname, string_agg(quote_literal(e.enumlabel), ', ' order by e.enumsortorder)
            from pg_enum e join pg_type t on t.oid = e.enumtypid join pg_namespace n on n.oid = t.typnamespace
           where n.nspname = 'ft_main' group by t.typname $q$) as t(typname text, labels text) loop
    if not exists (select 1 from pg_type t join pg_namespace n on n.oid = t.typnamespace
                    where n.nspname = 'ft_main' and t.typname = r.typname) then
      execute format('create type ft_main.%I as enum (%s)', r.typname, r.labels);
    end if;
  end loop;
  for r in select * from extensions.dblink(conn,
      $q$ select format('create sequence if not exists ft_main.%I', sequencename) from pg_sequences where schemaname = 'ft_main' $q$) as t(stmt text) loop
    execute r.stmt;
  end loop;
  for r in select * from extensions.dblink(conn,
      $q$ with tabs as (select c.oid, c.relname from pg_class c where c.relnamespace = 'ft_main'::regnamespace and c.relkind = 'r'),
               cols as (select t.relname, string_agg(format('%I %s%s%s', a.attname, format_type(a.atttypid, a.atttypmod),
                          case when a.attnotnull then ' not null' else '' end,
                          case when d.adbin is not null then ' default ' || pg_get_expr(d.adbin, d.adrelid) else '' end), ', ' order by a.attnum) as defs
                        from tabs t join pg_attribute a on a.attrelid = t.oid and a.attnum > 0 and not a.attisdropped
                        left join pg_attrdef d on d.adrelid = t.oid and d.adnum = a.attnum group by t.relname),
               cons as (select t.relname, string_agg(format(', constraint %I %s', k.conname, pg_get_constraintdef(k.oid)), '' order by k.contype desc, k.conname) as defs
                        from tabs t join pg_constraint k on k.conrelid = t.oid and k.contype in ('p', 'u', 'c') group by t.relname)
          select 1, format('create table if not exists ft_main.%I (%s%s)', c.relname, c.defs, coalesce(k.defs, '')) from cols c left join cons k on k.relname = c.relname
          union all select 2, format('alter table ft_main.%I add constraint %I %s', t.relname, k.conname, pg_get_constraintdef(k.oid)) from tabs t join pg_constraint k on k.conrelid = t.oid and k.contype = 'f'
          union all select 3, regexp_replace(pg_get_indexdef(i.indexrelid), '^CREATE INDEX', 'CREATE INDEX IF NOT EXISTS') from pg_index i join tabs t on t.oid = i.indrelid where not i.indisprimary and not i.indisunique
          union all select 4, format('alter sequence ft_main.%I owned by ft_main.%I.%I', s.relname, c.relname, a.attname) from pg_depend d join pg_class s on s.oid = d.objid and s.relkind = 'S' join pg_class c on c.oid = d.refobjid join pg_attribute a on a.attrelid = c.oid and a.attnum = d.refobjsubid where d.deptype = 'a' and s.relnamespace = 'ft_main'::regnamespace
          order by 1, 2 $q$) as t(ord int, stmt text) loop
    if r.ord = 2 and exists (select 1 from pg_constraint where conname = substring(r.stmt from 'add constraint (\S+)')) then
      continue;
    end if;
    execute r.stmt;
  end loop;
  grant usage, create on schema ft_main to ft_bot;
  grant usage on schema ft_main to authenticated, service_role;
  grant all on all tables in schema ft_main to ft_bot;
  grant usage, select, update on all sequences in schema ft_main to ft_bot;
  grant select on all tables in schema ft_main to authenticated, service_role;
  alter default privileges in schema ft_main grant select on tables to service_role;

  -- views and functions exactly as production has them now, with grants
  for r in select * from extensions.dblink(conn,
      $q$ select c.relname, pg_get_viewdef(c.oid, true), c.reloptions,
                 (select array_agg((case when a.grantee = 0 then 'public' else pg_get_userbyid(a.grantee) end) || ':' || a.privilege_type) from aclexplode(c.relacl) a)
            from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'v' $q$)
      as t(relname text, def text, opts text[], acl text[]) loop
    execute format('drop view if exists public.%I', r.relname);
    execute format('create view public.%I as %s', r.relname, r.def);
    if r.opts is not null then
      execute format('alter view public.%I set (%s)', r.relname, array_to_string(r.opts, ', '));
    end if;
    execute format('revoke all on public.%I from public, anon, authenticated, service_role', r.relname);
    foreach g in array coalesce(r.acl, '{}'::text[]) loop
      if split_part(g, ':', 1) in ('anon', 'authenticated', 'service_role') then
        execute format('grant %s on public.%I to %I', split_part(g, ':', 2), r.relname, split_part(g, ':', 1));
      end if;
    end loop;
  end loop;
  for r in select * from extensions.dblink(conn,
      $q$ select pg_get_functiondef(p.oid), p.oid::regprocedure::text,
                 (select array_agg((case when a.grantee = 0 then 'public' else pg_get_userbyid(a.grantee) end) || ':' || a.privilege_type) from aclexplode(p.proacl) a),
                 p.proacl is null
            from pg_proc p where p.pronamespace = 'public'::regnamespace and p.prokind = 'f' $q$)
      as t(def text, sig text, acl text[], acl_default boolean) loop
    execute r.def;
    if not r.acl_default then
      execute format('revoke all on function public.%s from public, anon, authenticated, service_role', r.sig);
      foreach g in array coalesce(r.acl, '{}'::text[]) loop
        if split_part(g, ':', 1) = 'public' then
          execute format('grant execute on function public.%s to public', r.sig);
        elsif split_part(g, ':', 1) in ('anon', 'authenticated', 'service_role') then
          execute format('grant execute on function public.%s to %I', r.sig, split_part(g, ':', 1));
        end if;
      end loop;
    end if;
  end loop;
end $$;

-- Then, separately and never from a committed file:
--   alter role ft_bot with password '<new staging password>';

-- ── C. data ───────────────────────────────────────────────────────────────
--
-- Every local base table that production also has is copied, auth users
-- included (same ids, same password hashes, so every owner_id stays valid
-- and nobody re-registers). Tables are ordered from the foreign-key graph;
-- where it has a cycle the foreign keys that close it are dropped for the
-- copy and added back, re-validated, at the end. User triggers on the public
-- tables are paused (the audit trigger would mint security_events ids that
-- collide with production's). auth.users is not ours to alter, so its sign-up
-- trigger runs and the profile rows it invents are deleted before
-- production's are copied. Sequences are advanced past the copied ids.
do $$
declare
  r record;
  cols text;
  progressed boolean;
  remaining integer;
begin
  if not exists (select 1 from pg_foreign_server where srvname = 'prod') then
    create server prod foreign data wrapper postgres_fdw
      options (host '<PROD_POOLER_HOST>', port '5432', dbname 'postgres', sslmode 'require');
  end if;
  if not exists (select 1 from pg_user_mappings where srvname = 'prod' and usename = current_user) then
    create user mapping for current_user server prod
      options (user 'postgres.<PROD_REF>', password '<PROD_PASSWORD>');
  end if;
  create schema if not exists prod_auth;
  create schema if not exists prod_public;
  create schema if not exists prod_ft_main;
  if not exists (select 1 from information_schema.tables where table_schema = 'prod_auth') then
    import foreign schema auth limit to (users, identities) from server prod into prod_auth;
  end if;
  if not exists (select 1 from information_schema.tables where table_schema = 'prod_public') then
    import foreign schema public from server prod into prod_public;
  end if;
  if not exists (select 1 from information_schema.tables where table_schema = 'prod_ft_main') then
    import foreign schema ft_main from server prod into prod_ft_main;
  end if;

  create temp table copy_plan on commit drop as
    select l.table_schema as s, l.table_name as t, false as done
      from information_schema.tables l
     where l.table_type = 'BASE TABLE'
       and (l.table_schema in ('public', 'ft_main')
            or (l.table_schema = 'auth' and l.table_name in ('users', 'identities')))
       and exists (select 1 from information_schema.tables f
                    where f.table_schema = 'prod_' || l.table_schema and f.table_name = l.table_name);
  create temp table dropped_fks (s text, t text, conname text, def text) on commit drop;

  for r in select s, t from copy_plan where s = 'public' loop
    execute format('alter table %I.%I disable trigger user', r.s, r.t);
  end loop;
  loop
    progressed := false;
    for r in select s, t from copy_plan where not done order by s, t loop
      if not exists (
        select 1 from pg_constraint c
          join pg_class p on p.oid = c.confrelid
          join pg_namespace pn on pn.oid = p.relnamespace
          join copy_plan d on d.s = pn.nspname and d.t = p.relname
         where c.contype = 'f' and c.conrelid = format('%I.%I', r.s, r.t)::regclass
           and not d.done and not (d.s = r.s and d.t = r.t)) then
        select string_agg(quote_ident(l.column_name), ', ' order by l.ordinal_position) into cols
          from information_schema.columns l
          join information_schema.columns f
            on f.table_schema = 'prod_' || r.s and f.table_name = r.t and f.column_name = l.column_name
         where l.table_schema = r.s and l.table_name = r.t and l.is_generated = 'NEVER';
        execute format('insert into %I.%I (%s) select %s from %I.%I on conflict do nothing',
                       r.s, r.t, cols, cols, 'prod_' || r.s, r.t);
        if r.s = 'auth' and r.t = 'users' then
          delete from public.profiles;
        end if;
        update copy_plan set done = true where s = r.s and t = r.t;
        progressed := true;
      end if;
    end loop;
    exit when not exists (select 1 from copy_plan where not done);
    if not progressed then
      insert into dropped_fks
        select r2.s, r2.t, c.conname, pg_get_constraintdef(c.oid)
          from copy_plan r2
          join pg_constraint c on c.conrelid = format('%I.%I', r2.s, r2.t)::regclass and c.contype = 'f'
          join pg_class p on p.oid = c.confrelid
          join pg_namespace pn on pn.oid = p.relnamespace
          join copy_plan d on d.s = pn.nspname and d.t = p.relname and not d.done
         where not r2.done;
      if not found then
        exit;
      end if;
      for r in select * from dropped_fks loop
        execute format('alter table %I.%I drop constraint if exists %I', r.s, r.t, r.conname);
      end loop;
    end if;
  end loop;
  select count(*) into remaining from copy_plan where not done;
  if remaining > 0 then
    raise exception 'could not order % table(s) for copy: %', remaining,
      (select string_agg(s || '.' || t, ', ') from copy_plan where not done);
  end if;
  for r in select * from dropped_fks loop
    execute format('alter table %I.%I add constraint %I %s', r.s, r.t, r.conname, r.def);
  end loop;
  for r in select s, t from copy_plan where s = 'public' loop
    execute format('alter table %I.%I enable trigger user', r.s, r.t);
  end loop;

  for r in
    select n.nspname as s, sq.relname as seq, c.relname as t, a.attname as col
      from pg_depend d
      join pg_class sq on sq.oid = d.objid and sq.relkind = 'S'
      join pg_class c on c.oid = d.refobjid
      join pg_namespace n on n.oid = sq.relnamespace
      join pg_attribute a on a.attrelid = c.oid and a.attnum = d.refobjsubid
     where d.deptype in ('a', 'i') and n.nspname in ('public', 'ft_main')
  loop
    execute format('select setval(%L, coalesce((select max(%I) from %I.%I), 0) + 1, false)',
                   format('%I.%I', r.s, r.seq), r.col, r.s, r.t);
  end loop;

  drop schema prod_auth cascade;
  drop schema prod_public cascade;
  drop schema prod_ft_main cascade;
  drop user mapping for current_user server prod;
  drop server prod;
end $$;

-- ── D. scrub the passwords the two blocks above left in the history ───────
update supabase_migrations.schema_migrations
   set statements = array[
     regexp_replace(
       regexp_replace(statements[1], 'password=[^ '']+', 'password=<redacted>', 'g'),
       'password ''[^'']+''', 'password ''<redacted>''', 'g')]
 where statements[1] like '%<PROD_POOLER_HOST>%' or statements[1] like '%pooler.supabase.com%';

-- Then run after_clone.sql, and rotate production's database password.
