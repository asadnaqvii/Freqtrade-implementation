-- 0008_views_and_catalog.sql
-- Read models for the UI, plus the seed data that makes the strategy builder
-- usable out of the box.

-- All views use security_invoker so the caller's RLS applies. Without it a view
-- runs as its owner and would happily hand one user another user's backtests.

create or replace view public.v_strategy_performance
with (security_invoker = on) as
select s.id                              as strategy_id,
       s.owner_id,
       s.name,
       s.class_name,
       count(r.id)                       as backtest_count,
       max(r.created_at)                 as last_backtest_at,
       avg(r.profit_total_pct)           as avg_profit_pct,
       max(r.profit_total_pct)           as best_profit_pct,
       min(r.profit_total_pct)           as worst_profit_pct,
       avg(r.win_rate)                   as avg_win_rate,
       avg(r.max_drawdown_pct)           as avg_max_drawdown_pct,
       avg(r.sharpe)                     as avg_sharpe,
       sum(r.total_trades)               as total_backtested_trades
  from public.strategy_specs s
  left join public.backtest_runs r on r.strategy_id = s.id
 where not s.is_archived
 group by s.id, s.owner_id, s.name, s.class_name;

create or replace view public.v_bot_health
with (security_invoker = on) as
select b.id,
       b.owner_id,
       b.name,
       b.exchange,
       b.strategy,
       b.trading_mode,
       b.status,
       b.last_heartbeat_at,
       extract(epoch from (now() - b.last_heartbeat_at))::numeric as heartbeat_age_seconds,
       -- A bot that has not checked in for 5 minutes is not "running", whatever
       -- its status column claims.
       case
         when b.last_heartbeat_at is null then 'never_seen'
         when b.last_heartbeat_at > now() - interval '5 minutes' then 'healthy'
         when b.last_heartbeat_at > now() - interval '30 minutes' then 'stale'
         else 'offline'
       end as health,
       (select count(*) from public.trade_archive t
         where t.bot_instance_id = b.id and t.is_open) as open_trades
  from public.bot_instances b;

create or replace view public.v_account_verification
with (security_invoker = on) as
select a.id                as account_id,
       a.owner_id,
       a.label,
       a.provider,
       a.provider_kind,
       a.is_active,
       a.is_sandbox,
       a.last_verified_at,
       a.last_verification,
       extract(epoch from (now() - a.last_verified_at))::numeric as verified_age_seconds,
       r.id                as last_run_id,
       r.status            as last_run_status,
       r.egress_region     as last_egress_region,
       r.summary           as last_summary,
       r.checks_failed     as last_checks_failed
  from public.exchange_accounts a
  left join lateral (
    select * from public.validation_runs vr
     where vr.account_id = a.id
     order by vr.created_at desc
     limit 1
  ) r on true;

create or replace view public.v_trade_pnl_daily
with (security_invoker = on) as
select t.owner_id,
       t.bot_instance_id,
       date_trunc('day', t.close_date) as day,
       count(*)                        as trades,
       count(*) filter (where t.close_profit_abs > 0) as wins,
       count(*) filter (where t.close_profit_abs < 0) as losses,
       sum(t.close_profit_abs)         as profit_abs,
       avg(t.close_profit_pct)         as avg_profit_pct
  from public.trade_archive t
 where not t.is_open and t.close_date is not null
 group by t.owner_id, t.bot_instance_id, date_trunc('day', t.close_date);

-- ---------------------------------------------------------------------------
-- Seed: the indicator vocabulary the builder offers
-- ---------------------------------------------------------------------------
insert into public.indicator_catalog
  (key, label, category, description, params_schema, outputs, min_startup_candles, sort_order)
values
  ('rsi', 'RSI', 'momentum',
   'Relative Strength Index. Oscillates 0-100; below 30 is commonly read as oversold, above 70 as overbought.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":200,"default":14}},"required":["period"]}',
   array['rsi_{period}'], 30, 10),

  ('ema', 'EMA', 'trend',
   'Exponential moving average. Reacts faster than SMA to recent price.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":400,"default":21},"source":{"type":"string","enum":["open","high","low","close"],"default":"close"}},"required":["period"]}',
   array['ema_{period}'], 400, 20),

  ('sma', 'SMA', 'trend',
   'Simple moving average over the last N candles.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":400,"default":50},"source":{"type":"string","enum":["open","high","low","close"],"default":"close"}},"required":["period"]}',
   array['sma_{period}'], 400, 30),

  ('macd', 'MACD', 'momentum',
   'Moving average convergence/divergence. Emits the MACD line, its signal line and the histogram.',
   '{"type":"object","properties":{"fast":{"type":"integer","minimum":2,"maximum":100,"default":12},"slow":{"type":"integer","minimum":3,"maximum":200,"default":26},"signal":{"type":"integer","minimum":2,"maximum":100,"default":9}},"required":["fast","slow","signal"]}',
   array['macd', 'macdsignal', 'macdhist'], 200, 40),

  ('bbands', 'Bollinger Bands', 'volatility',
   'Moving average with standard-deviation bands. Emits upper, middle, lower and %B width.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":5,"maximum":200,"default":20},"stddev":{"type":"number","minimum":0.5,"maximum":5,"default":2.0}},"required":["period","stddev"]}',
   array['bb_lower', 'bb_middle', 'bb_upper', 'bb_percent', 'bb_width'], 200, 50),

  ('atr', 'ATR', 'volatility',
   'Average true range. The usual building block for volatility-scaled stops.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":100,"default":14}},"required":["period"]}',
   array['atr_{period}'], 100, 60),

  ('adx', 'ADX', 'trend',
   'Average directional index. Measures trend strength without direction; above 25 is commonly read as trending.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":100,"default":14}},"required":["period"]}',
   array['adx_{period}'], 100, 70),

  ('stoch', 'Stochastic', 'momentum',
   'Stochastic oscillator (slow). Emits %K and %D.',
   '{"type":"object","properties":{"fastk":{"type":"integer","minimum":2,"maximum":100,"default":14},"slowk":{"type":"integer","minimum":1,"maximum":50,"default":3},"slowd":{"type":"integer","minimum":1,"maximum":50,"default":3}},"required":["fastk","slowk","slowd"]}',
   array['stoch_k', 'stoch_d'], 100, 80),

  ('cci', 'CCI', 'momentum',
   'Commodity channel index. Typically read as extreme beyond +/-100.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":200,"default":20}},"required":["period"]}',
   array['cci_{period}'], 200, 90),

  ('mfi', 'Money Flow Index', 'volume',
   'Volume-weighted RSI. Needs volume data to be meaningful.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":100,"default":14}},"required":["period"]}',
   array['mfi_{period}'], 100, 100),

  ('volume_mean', 'Average Volume', 'volume',
   'Rolling mean of volume. Use it to require that a signal happens on real participation.',
   '{"type":"object","properties":{"period":{"type":"integer","minimum":2,"maximum":200,"default":20}},"required":["period"]}',
   array['volume_mean_{period}'], 200, 110),

  ('price', 'Price / OHLCV', 'price',
   'The raw candle columns. No parameters; always available.',
   '{"type":"object","properties":{}}',
   array['open', 'high', 'low', 'close', 'volume'], 0, 5)
on conflict (key) do update
  set label               = excluded.label,
      category            = excluded.category,
      description         = excluded.description,
      params_schema       = excluded.params_schema,
      outputs             = excluded.outputs,
      min_startup_candles = excluded.min_startup_candles,
      sort_order          = excluded.sort_order;
