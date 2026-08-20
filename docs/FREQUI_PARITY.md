# FreqUI parity — what to replicate on the Live bot page

Reference captured from the running FreqUI (TrendPullbackStrategy, 4h, KuCoin)
so the Live bot page can be measured against something concrete rather than
against memory.

FreqUI's layout is two columns: a **Multi Pane** on the left holding controls and
seven switchable panels, and a **Chart** on the right with **Open Trades** beneath
it.

## Controls (top of Multi Pane)

Six buttons: start, stop, pause, reload config, force-exit-all, force-enter.

| Control | Status here | Note |
|---|---|---|
| start | done | `/api/live/control` |
| stop | done | halts entirely; open positions stop being managed |
| pause / stopentry | done | stops opening new trades, keeps managing held ones — the safe stop |
| reload config | done | |
| force exit (per trade) | done | on each open position |
| force exit all | not done | |
| force enter | **deliberately absent** | opening a position from a web page is a different kind of exposure from closing one; entries are the strategy's job |

## Multi Pane panels

1. **Pair list** — every whitelisted pair, with a filter box. Selecting one drives
   the chart. *Shown as the current whitelist; no filter and it drives nothing yet,
   because there is no chart for it to drive.*
2. **Bot info** — version, state, strategy, timeframe, stake, max open trades,
   dry-run flag. *Done, as the status bar.*
3. **Performance** — four sub-tabs, each `Name | Profit % | Profit USDT | Count`:
   - Performance: by pair. *Done.*
   - Entries: by entry tag. *Done.*
   - Exits: by exit reason. *Done.*
   - Mix Tag: entry+exit tag combinations. *Done.*
4. **Balance** — per currency. *Done.*
5. **Period Breakdown** — Days / Weeks / Months, and Abs $ / Rel % toggles. A
   combined chart (profit line over trade-count bars) plus a table of
   `Day | Profit | In USD | Trades | Profit%`. *Only a daily profit chart here;
   no weeks/months, no table, no trade counts.*
   - Note: FreqUI puts profit and trade count on two y-axes. Deliberately not
     copying that — a second scale invites reading the crossing point as
     meaningful when it is an artefact of the scaling. Two aligned plots instead.
6. **Logs** — *Done.*
7. **Locks** — pairs the strategy has locked after a loss, and until when.
   *Endpoint allowlisted, no panel yet.*

## Chart (right column)

Candlesticks with volume underneath, a pair selector, and a range slider. Overlaid:

- entry markers (green triangles) and exit markers (blue crosses)
- a label on each closed trade showing its profit percent
- shaded region while a trade is open, with duration and bar count
- crosshair tooltip listing open/high/low/close, volume, and any entry or exit
  on that candle
- "Show Chart Areas" and "Heikin Ashi" toggles, and a plot-config selector

Fed by `/api/v1/pair_candles`, which returns OHLCV plus the strategy's own
indicator columns and the trade markers.

*Not done, and the largest remaining gap.*

## Open Trades (below the chart)

`ID | Pair | Amount | Stake amount | Open rate | Current rate | Current profit % |
Open date`. *Done, plus held-duration and a close button.*

## From the Dashboard view

- **Profit over time combined** — days/weeks/months, absolute or relative. *Done.*
- **Cumulative profit** — the running total across trades. *Done, as the line over
  the period bars.*
- **Wallet history** — account value over time, from `/historic_balance`. *Done.*
- **Closed trades** with close reason. *Done.*
- **Bot comparison** — one row per bot. Not replicated: there is one bot. The
  table exists in the database (`bot_instances`) if that changes.
