-- after_clone.sql
--
-- Run once on a staging database that was copied from production, after the
-- data copy (copy_from_prod.sql) and before the staging bot's first boot.
-- Idempotent.
--
-- A copy of production is only useful as a place to test if it is also a place
-- that cannot act on the live account. The data copy brings over three things
-- that would let it: the production bot rows, the open positions, and the
-- login of the role freqtrade connects as. Each is dealt with here.

-- 1. The copied production bot rows become history. The staging bot registers
--    itself under its own name (BOT_NAME=freqtrade-bot-staging), and the
--    watchdog ignores retired rows, so nothing pages about a "bot" that was
--    never going to heartbeat here.
update public.bot_instances
   set retired_at = now(),
       status = 'retired'
 where retired_at is null;

-- 2. No inherited positions. The staging bot runs dry-run; a live position it
--    "inherited" would be sold with a simulated fill, corrupting the copied
--    history and confusing the dashboard. Closed trades stay: they are real
--    history, attributed to the retired production bot id, and they are what
--    the per-strategy comparison shows on day one.
delete from ft_main.orders
 where ft_trade_id in (select id from ft_main.trades where is_open);
delete from ft_main.trade_custom_data
 where ft_trade_id in (select id from ft_main.trades where is_open);
delete from public.trade_archive
 where ft_trade_id in (select id from ft_main.trades where is_open);
delete from ft_main.trades where is_open;

-- 3. Nothing is "wanted running" from the production dashboard's last click.
update public.bot_instances
   set metadata = metadata - 'desired_state' - 'verify_requested_at';

-- 4. The role freqtrade connects as needs its own password here. The copy does
--    not carry passwords, and the production one must never be reused.
--    Set it separately, never from a file that is committed:
--
--        alter role ft_bot with password '<new staging password>';
--
-- 5. exchange_accounts stays as it is: the row holds the NAMES of environment
--    variables, never keys, and the staging bot has none of those variables set.
--    Credential checks therefore fail on staging, which is the expected and
--    correct result.
