# TrendPullbackStrategy_v3 — evaluated, not deployed

Added to `strategies/` on 2026-08-25 and verified to load. **Not running
anywhere.** The backtests below are why.

## What v3 changes

Three things, per the file's own header:

1. **BTC regime filter** — no entries on any pair unless BTC's own fast EMA is
   above its slow EMA. Pulled via `informative_pairs`, so it works whether or
   not BTC is tradeable.
2. **Structural-loser blacklist** — ZEC, UNI, SHIB, PEPE, AAVE removed in the
   accompanying config.
3. **Risk per trade 1.0% → 1.3%**, alongside capital moving to $2000.

## Result

KuCoin, 4h, six pairs (BTC, ETH, SOL, XMR, LINK, DOGE), $2000 starting balance.
Two windows, so a single favourable period cannot carry the conclusion.

### One year, 2025-08-25 → 2026-08-25

| | v1 | v3 |
|---|---:|---:|
| Trades | 173 | 103 |
| Profit | **−301.89 (−15.09%)** | **−408.35 (−20.42%)** |
| Per trade | −1.75 | −3.96 |
| Max drawdown | 21.34% | 23.72% |
| Profit factor | 0.74 | 0.58 |
| Win rate | 67.6% | 63.1% |

### Two years, 2024-08-25 → 2026-08-25

| | v1 | v3 |
|---|---:|---:|
| Trades | 510 | 397 |
| Profit | **−560.51 (−28.03%)** | **−688.63 (−34.43%)** |
| Per trade | −1.10 | −1.73 |
| Max drawdown | 35.77% | 40.01% |
| Profit factor | 0.80 | 0.77 |
| Win rate | 67.5% | 65.5% |

v3 is worse on every measure, in both windows.

## Reading it

**The BTC filter is doing something, and it is not helping.** It cuts trade
count by 40% and 22%, which is what a regime filter should do. But profit factor
falls with it. A filter that removes trades and lowers the quality of what
remains is removing the wrong trades.

**The size increase is not the whole story.** 1.0% → 1.3% risk is 1.3× more
size, but v3's per-trade loss is 2.3× v1's over one year. Bigger positions
explain part of the wider loss, not most of it.

**Both versions lose money, and that is the finding worth acting on.** A ~67%
win rate with a profit factor of 0.74 is a specific, diagnosable shape: it wins
often and small, loses rarely and big. The trailing stop (`trailing_stop_positive
0.015`, offset `0.03`) clips winners at around 1.5% while the stoploss lets
losers run to 6%, or to whatever `custom_stoploss` returns from
`atr_pct × 2.5`. Roughly four winners are needed to pay for one loser, and 67%
does not clear that. Neither v1 nor v3 changes this, so neither can fix it.

## What is not tested here

- **The blacklist.** The six pairs used contain none of ZEC, UNI, SHIB, PEPE or
  AAVE, so its benefit is untested. Testing it needs a pair set that includes
  them, run both with and without.
- **The live pair universe.** The bot uses `VolumePairList` over 25 pairs chosen
  dynamically. A fixed six-pair backtest is not that.
- **Partial profit-taking under `available_capital: 2000`.** The backtests used a
  flat stake; live sizing comes from `custom_stake_amount`.

## Config notes

The accompanying `config_v3.json` needs three changes before it could go live,
recorded here so they are not rediscovered later:

- `"api_server": {"enabled": false}` — the dashboard reaches the bot over this.
  Disabling it makes the bot invisible to everything in this repo.
- `"dry_run": true` — states paper trading.
- `"available_capital": 2000` — the account held 879.86 USDT free at the time of
  writing, plus three open positions. Declaring 2000 tells freqtrade to size
  against capital that is not there.

Also worth noting: the header says BTC is blacklisted "as a TRADED pair", but
`pair_blacklist` in the config does not contain `BTC/USDT`. The stated intent
and the config disagree.
