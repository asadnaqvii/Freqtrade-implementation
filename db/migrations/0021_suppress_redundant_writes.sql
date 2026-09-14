-- 0021_suppress_redundant_writes.sql
--
-- The signal capture and archive sync are written as upserts so that calling
-- them on every check is safe. Logically that is true: re-reading an
-- overlapping candle window changes nothing. Physically it is not.
--
-- ON CONFLICT DO UPDATE writes a new row version whether or not any value
-- differs. Postgres has no way to know the incoming row is identical, so it
-- writes the tuple, updates every index, emits WAL, and leaves a dead tuple for
-- autovacuum. As of 2026-09-04 that had produced:
--
--   strategy_signals   81,103 updates over    730 distinct rows
--   trade_archive      24,751 updates over     76 distinct rows
--
-- 105,854 row rewrites to store 806 rows. Closed trades never change and a
-- finished candle never changes, so essentially all of it was rewriting values
-- with themselves.
--
-- suppress_redundant_updates_trigger() is a core Postgres function for exactly
-- this: a BEFORE UPDATE row trigger that compares the new tuple with the old
-- and returns NULL -- skipping the write entirely -- when they are identical.
-- Doing it here rather than in the client means no future caller has to
-- remember, and a genuinely changed row still writes normally.
--
-- Neither table has an updated_at trigger, so nothing stamps now() onto the row
-- ahead of the comparison and makes every tuple look different. Any table given
-- this trigger later must be checked for that first: trigger order is
-- alphabetical by name, and a set_updated_at that fires first would defeat it.

create trigger a_strategy_signals_skip_noop
  before update on public.strategy_signals
  for each row execute function suppress_redundant_updates_trigger();

create trigger a_trade_archive_skip_noop
  before update on public.trade_archive
  for each row execute function suppress_redundant_updates_trigger();
