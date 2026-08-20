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
   the chart. *Shown as the current whitelist, and a dropdown of the same pairs
   (plus anything currently held, whether or not a filter has since dropped it)
   drives the chart. No filter box: a dropdown of eight pairs does not need one.*
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
   `Day | Profit | In USD | Trades | Profit%`. *Done: Days/Weeks/Months toggle,
   bars per period with the running total over them, and the table beneath.*
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

*Done.* `GET /api/live/candles` proxies `pair_candles` through the same
owner-scoped bot lookup as every other live read, and the page draws it as inline
SVG — candles, volume, entry triangles, exit crosses, per-candle hover, shaded
bands for the time a position was held, and a profit label on each closed trade.

Four deliberate differences from FreqUI:

- **Signals and fills are drawn as different marks.** A triangle is what the
  strategy *said*; a filled circle is what the bot *did*. A signal with no circle
  under it is one it could not act on — no free slot, or not enough stake — and
  that gap is worth being able to see at a glance. FreqUI draws both as one mark.
- **No range slider.** A candle-count selector (200–1500) instead. The slider
  re-crops data already fetched; the selector decides how much to fetch, which is
  the choice that actually costs anything over the private network.
- **No Heikin Ashi or plot-config selector.** Both are strategy-authoring tools,
  and this page is for watching a bot that is already running.
- **Redraws at most once a minute**, not on the 15-second live poll. Five hundred
  candles is a few hundred kilobytes and re-rendering throws away whatever the
  cursor was hovering; prices in the panels above still move every 15 seconds.

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

## Beyond FreqUI

- **Trade history in the database** — the same closed trades read from Postgres
  rather than from the bot. FreqUI has no equivalent because it has no database
  behind it: its history is whatever the running process holds. This is the copy
  that survives a redeploy, and the only one still there when the bot is down.

## Still outstanding

- **Locks panel** — `locks` is allowlisted on the bot client and the endpoint
  answers; there is no panel rendering it.
- **Force exit all** — deliberate: closing every position at once is a decision
  that wants more friction than a button, and closing them one at a time works.
- **Two-column layout** — FreqUI puts the Multi Pane beside the chart. Everything
  in that pane exists here, stacked rather than side by side.
