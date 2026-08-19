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

Use the **session pooler** URI from the Supabase dashboard, not the direct
connection:

```
postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres
```

`db.<ref>.supabase.co` resolves to IPv6 only, and Render cannot reach it. The
symptom is a connection timeout that looks like a firewall problem.

You do not need to add a schema parameter. The bot appends
`?options=-c search_path=ft_main,public` itself, which is what keeps freqtrade's
tables out of the API surface.

## Order of operations

1. Apply `db/migrations/0001` … `0012` to the Supabase project.
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
