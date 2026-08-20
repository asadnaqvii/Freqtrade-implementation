-- Let the bot's own service-role client read freqtrade's tables.
--
-- v_live_trades and v_live_orders are created `with (security_invoker = on)`,
-- which is what keeps RLS meaningful for dashboard users: the view reads as the
-- caller, not as its owner. The consequence is that SELECT on the view is not
-- enough -- the caller also needs SELECT on ft_main.trades / ft_main.orders.
--
-- service_role had the first and not the second, so the bot's reconciliation
-- pass got `permission denied for relation orders` on every run and logged it
-- as "no order history to reconcile yet". A verification step that quietly
-- reports nothing to check is worse than one that fails loudly: it reads as a
-- clean bill of health.
--
-- Idempotent; safe to re-run.

grant usage on schema ft_main to service_role;
grant select on all tables in schema ft_main to service_role;

-- freqtrade creates its tables on first connect and recreates them after a
-- schema reset, so grant forward as well as backward.
alter default privileges in schema ft_main grant select on tables to service_role;
