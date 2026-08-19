-- Record what was asked for, not just what ran.
--
-- A backtest request names a window. The data available may be shorter -- an
-- exchange keeps far less 5m history than 1d history, and freqtrade will
-- happily backtest whatever it finds. Until now only the window that ran was
-- stored, so a request for ten years that tested twenty-nine days was
-- indistinguishable from one that got what it asked for.
--
-- Storing the request alongside the result makes the gap visible and, more
-- importantly, checkable: the verdict can compare them and refuse to present a
-- month as a decade.

alter table public.backtest_runs
  add column if not exists requested_timerange text,
  add column if not exists coverage_pct numeric(6, 2),
  add column if not exists coverage_note text;

comment on column public.backtest_runs.requested_timerange is
  'The timerange the job asked for, verbatim (YYYYMMDD-YYYYMMDD). Null means '
  '"whatever the venue had".';
comment on column public.backtest_runs.coverage_pct is
  'How much of the requested window the data actually covered, 0-100. Null when '
  'no window was requested.';
comment on column public.backtest_runs.coverage_note is
  'Plain-language explanation when coverage fell short, including the likely '
  'cause -- usually that the venue does not keep that timeframe back that far.';
