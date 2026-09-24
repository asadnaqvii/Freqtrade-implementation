# Deployment

Three Render services, all in **`singapore`**.

| Service | Type | Public? | Holds API keys? | Does |
|---|---|---|---|---|
| `freqtrade-bot` | `pserv` (private service) | No | Yes | Trades |
| `freqtrade-app` | `web` | Yes | No | API + dashboard |
| `freqtrade-worker` | `worker` + 10 GB disk | No | No | Runs backtests |

## Why the region is not optional

KuCoin blocks requests from US IP addresses. Render defaults to **Oregon (US)**
when a service is created without a region, which is why this deployment failed
on Render while the identical code worked on Railway — Railway happened to place
it outside the US. An earlier attempt to route around this with an HTTP proxy
(commit `57e8991`) was reverted.

**A Render service's region is fixed when the service is created and cannot be
changed afterwards.** A service created in the wrong region has to be deleted and
recreated. `singapore` and `frankfurt` both work; `ohio`, `oregon` and `virginia`
do not.

Render's private network also only links services in the same region, so the app
can only reach the bot because all three share one.

To confirm the region is right after deploying, run the connectivity check from
the dashboard's Verification tab. `provider.egress_region` reports the country
the request actually left from.

## Why the bot is private

`type: pserv` gives the bot no public URL. Nothing on the internet can reach it,
including FreqUI. That is deliberate: the bot is the only process holding
exchange API keys, and its REST API can place orders.

The app service is the front door. It reaches the bot at
`http://freqtrade-bot:8080` over Render's private network, and it reads the
bot's state from Supabase rather than by proxying it.

One consequence worth knowing before you deploy: **private services are not
available on Render's free tier.** All three services here are on `starter`.

## Environment variables

### `freqtrade-bot`

| Variable | Required | Notes |
|---|---|---|
| `FREQTRADE__EXCHANGE__KEY` | yes | Set in the dashboard, never in `render.yaml` |
| `FREQTRADE__EXCHANGE__SECRET` | yes | |
| `FREQTRADE__EXCHANGE__PASSWORD` | yes | KuCoin's passphrase. Missing it produces a signature error that reads like a wrong secret |
| `SUPABASE_DB_URL` | yes | Session pooler URI — see below |
| `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY` | no | Only for registration and heartbeat |
| `PLATFORM_OWNER_ID` | no | Your `profiles.id`, so the bot's rows are owned by you |
| `FREQTRADE_DB_SCHEMA` | no | Defaults to `ft_main` |
| `DRY_RUN` | no | `true` to paper trade |

### `freqtrade-app`

`SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_ROLE_KEY`,
`SUPABASE_JWT_SECRET`.

The JWT secret is what lets the API verify access tokens itself instead of
calling Supabase on every request. Find it under **Project Settings → API → JWT
Secret**. Without it every authenticated endpoint returns 500.

### `freqtrade-worker`

`SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`. The disk is mounted at `/data` and
caches candle data between runs.

## The database URL

Use the **direct** connection, with the project's **IPv4 add-on** enabled
(Settings → Add-ons → IPv4, $4/month):

```
postgresql://ft_bot:<password>@db.<ref>.supabase.co:5432/postgres
```

Without the add-on `db.<ref>.supabase.co` resolves to IPv6 only, which Render
cannot reach -- the symptom is a connection timeout that looks like a firewall
problem. The session pooler is IPv4 by nature, and was the original path; it
is also where every crash of this bot began (dropped connections, checkout
timeouts, authentication hiccups), so it is now the fallback rather than the
route. Put its URI in `SUPABASE_DB_URL_FALLBACK`:

```
postgresql://ft_bot.<ref>:<password>@aws-1-<region>.pooler.supabase.com:5432/postgres
```

Note the username: `ft_bot` on the direct host, `ft_bot.<ref>` through the
pooler. The bot tries the fallback only when the direct host does not answer
at boot, and says so in its log.

The bot appends TCP keepalives, a 10-second `connect_timeout` and an
`application_name` to whichever URL it uses, so its connections are visible
by name in `pg_stat_activity`. The schema is not in the URL: the `ft_bot` role
carries `search_path = ft_main, public` as a server-side default, which is the
one form that survives both routes.

## Alerting

Two things watch the bot, and neither depends on Supabase being up:

- `HEARTBEAT_URL` on the bot -- a [healthchecks.io](https://healthchecks.io)
  ping URL with a 10-minute grace period. The bot pings it every two minutes
  **only while it is verifiably trading** (state running, trading loop going
  round). Anything else -- a crash, a hung process, a deliberate stop, Supabase
  unreachable, Render down -- is silence, and silence sends the email.
- `WORKER_HEARTBEAT_URL` on the worker -- the same, for the process that runs
  the watchdog, so the watcher is watched.
- `ALERT_WEBHOOK_URL` on the worker -- where the watchdog posts incidents
  (offline, not trading) and repeats them every 30 minutes while they stay
  open. A healthchecks.io check's `/log` URL works, as does any JSON webhook.

## Order of operations

1. Apply every file in `db/migrations/` in order -- see `db/migrations/README.md`
   for the two exceptions (0009 is production-only, 0014 is not yet applied anywhere).
2. Migrate the existing trade history — see `DATA_MIGRATION.md`. Do this
   **before** pointing the bot at Postgres.
3. Deploy the blueprint, setting the secrets above.
4. Sign in to the app, connect your wallet, run a verification.
5. Confirm `provider.egress_region` is not `US`.

## Verifying a deploy

`GET /api/health` on the app service reports what is configured without
disclosing any of it:

```json
{"status":"ok","supabase_configured":true,"jwt_verification":true,
 "database_url_set":true,"bot":{"db_schema":"ft_main","api_reachable_at":true}}
```

`jwt_verification: false` means `SUPABASE_JWT_SECRET` is missing and nobody can
sign in. `database_url_set: false` on the bot means it is still writing to
ephemeral SQLite.
