# Using and testing the platform

Two deployments, one code base. **Production** trades real money on KuCoin
from `production-2`. **Staging** is a copy of production that trades in dry
run with no exchange keys, from the `staging` branch, and is where every
change lands first.

| | Production | Staging |
|---|---|---|
| Dashboard | https://freqtrade-app.onrender.com | https://freqtrade-app-staging.onrender.com |
| Sign in | the same account works on both (staging is a copy of the user table) | |
| Bot | `freqtrade-bot` (private) | `freqtrade-bot-staging` (private, dry run) |
| Database | Supabase `ytcmkwyitloysexxqwof` | Supabase `wltovgvpvnsbpzfxtpdu` |
| Branch | `production-2` | `staging` |

Staging shows a banner at the top of every page. If you do not see it, you
are on production.

## The dashboard, tab by tab

**Strategy builder.** Write or edit a strategy specification, compile it,
and save it as a version. Nothing here touches the bot.

**Backtesting.** Queue a backtest of any saved strategy over any window. The
worker runs it and stores the results; the comparison table ranks strategies
against each other.

**Verification.** Connect a wallet by the *names* of the environment
variables holding its keys (the keys themselves never leave the bot), run a
credential check, and read the three-way chain: what the strategy said, what
the bot did, what the exchange confirms. A row marked *not acted on* now
names the reason, taken from the Learning Module.

**Live bot.** The bot's state, balance, positions, orders, the pairs it
watches and the ones locked out, and the incident banner. Start, stop and
"stop opening new trades" act on the bot through its private API. Stopping
outright leaves open positions unmanaged; the banner says so.

**Trading memory.** Every decision the bot made, why, and what came of it.
See `LEARNING.md`.

## Testing a change

1. Push to `staging`. The three staging services redeploy on their own within
   a few minutes. Watch the staging bot's log for `holding the trading lock`
   and `started it (running)`.
2. Use the staging dashboard. The bot trades the real market in dry run, so
   signals, decisions, dry-run orders and fills all happen on their own; a
   4h strategy produces a handful a day.
3. Run the tests locally before pushing: `.venv/bin/pytest tests/ -q` (a few
   hundred tests, about twenty seconds).
4. When staging has run clean for as long as the change deserves, merge
   `staging` into `production-2` and push. Production redeploys with a
   handover: the old instance stops trading and releases the lock, the new
   one takes it. Watch for the same two log lines.

## Testing the Learning Module

With `LEARNING_ENABLED=true` on the staging bot:

- Within a minute of a deploy the log says `learning: recording trading
  decisions to user_data/learning_outbox.sqlite`.
- Every ten minutes it prints a one-line report: decisions and events
  recorded, adapter errors (should stay at 0), outbox pending (should stay
  near 0), and whether the writer is alive.
- The Trading memory tab fills as the strategy signals. A locked pair, a
  full book or a paused bot all produce a decision with a rejection reason;
  an executed entry produces the full chain signal, instruction, order,
  acknowledgement, fill, position.
- Health at the top of the tab, or `/api/learning/health`: pending near 0,
  quarantined 0, events without a decision 0.
- Without waiting for the market: `SUPABASE_URL=... SUPABASE_SERVICE_ROLE_KEY=...
  python scripts/learning_smoke.py --environment staging` ships a made-up
  decision and its events and reads them back. Never run it against
  production.

## Alerts

Set a healthchecks.io ping URL as `HEARTBEAT_URL` on the bot,
`WORKER_HEARTBEAT_URL` on the worker, and its `/log` endpoint as
`ALERT_WEBHOOK_URL` on the worker. The bot pings only while it is verifiably
trading, so silence for any reason reaches you by email; the worker pages on
`offline` and `not_trading` and again every thirty minutes while they last.
