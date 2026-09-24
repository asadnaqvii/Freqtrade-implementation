-- 0011_harden_graphql_and_invoker.sql
-- The last two linter findings after 0010.
--
-- 1. current_profile_id() was SECURITY DEFINER only so it could survive the auth
--    schema being absent (bare-Postgres dev). It reads auth.uid() and nothing
--    else, which any signed-in caller may already do, so DEFINER buys nothing
--    and shows up as an escalation-shaped warning. Downgrade to INVOKER.
--
-- 2. Every app table showed as "visible in the GraphQL schema to signed-in
--    users". That one is inherent to RLS: an authenticated user *must* hold
--    SELECT for the policies to then filter rows down to their own. The
--    warning is about discoverability of table names, not row access.
--    This app talks to PostgREST, not GraphQL, so the honest fix is to stop
--    exposing the GraphQL endpoint at all rather than to strip the SELECT
--    grants the app depends on.

alter function public.current_profile_id() security invoker;

-- Turn off the GraphQL API. REST (PostgREST) is unaffected -- that is what
-- supabase-js uses unless you explicitly call .graphql().
-- To re-enable: grant usage on schema graphql to anon, authenticated;
do $$
begin
  if to_regnamespace('graphql') is not null then
    revoke usage on schema graphql from anon, authenticated;
  end if;
  if to_regnamespace('graphql_public') is not null then
    revoke usage on schema graphql_public from anon, authenticated;
  end if;
end $$;
