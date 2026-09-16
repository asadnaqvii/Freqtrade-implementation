# Staging

A second copy of the whole platform -- database, bot, dashboard, worker --
that runs the same code on a copy of production's data and **cannot act on the
live account**. Features are built and proven here before they reach
`production-2`.

| | Production | Staging |
|---|---|---|
| Git branch | `production-2` | `staging` (production-2 plus whatever is being proven) |
| Supabase project | `ytcmkwyitloysexxqwof` (Freqtrader bot) | `wltovgvpvnsbpzfxtpdu` (freqtrade-staging), same org, Tokyo |
| Render services | `freqtrade-bot`, `freqtrade-app`, `freqtrade-worker` | `freqtrade-bot-staging` (`srv-dal542m1egvs73eimam0`), `freqtrade-app-staging` (`srv-dal543ijnfac73cgv910`), `freqtrade-worker-staging` |
| Bot | live, holds exchange keys | `DRY_RUN=true`, **no exchange keys**, `BOT_NAME=freqtrade-bot-staging` |
| Database URL | direct host (IPv4 add-on) | `ft_bot.wltovgvpvnsbpzfxtpdu@aws-0-ap-northeast-1.pooler.supabase.com:5432` |
| Alerts | healthchecks.io | none, by design |

## What makes it safe

Four independent things, any one of which is enough:

1. **No exchange credentials.** The staging bot's environment has no
   `FREQTRADE__EXCHANGE__KEY/SECRET/PASSWORD`. Dry-run uses public market data
   only; there is nothing to place an order with.
2. **The bot refuses to trade live outside production.** `render_start.py`
   exits before writing a config if `ENVIRONMENT` is not `production` and
   `DRY_RUN` is not `true`, and warns if a dry run is carrying credentials.
3. **Its own database.** `v_live_trades`/`v_live_orders` are global view names
   and the trading lock is keyed on `BOT_NAME`, so a second bot on the
   production database would repoint the live dashboard and could evict the
   production bot. Staging never points at production; `scripts/render_audit.py`
   fails if any `-staging` service shares `SUPABASE_URL` or `SUPABASE_DB_URL`
   with a production one.
4. **Its own name.** `BOT_NAME=freqtrade-bot-staging` keys both the
   `bot_instances` row and the advisory trading lock.

The dashboard shows a STAGING banner above everything, before sign-in, read
from `GET /api/config`.

## How the database was made

Not from the dashboard's "Restore to a New Project" (dashboard-only, needs
physical backups) and not from `pg_dump` (the direct host is IPv6-only and the
local `pg_dump` is v16 against a PG17 server). Everything ran **inside the new
project's Postgres** over `dblink`/`postgres_fdw`, through the pooler, so
nothing left Supabase. `db/staging/copy_from_prod.sql` is the script, with
placeholders; it was applied through the MCP `apply_migration` tool as the
`postgres` role. In order:

1. **Schema** -- production's `supabase_migrations` table holds every migration
   exactly as applied, including two hand fixes that never became files, so
   staging replays that history statement for statement (skipping the two
   entries about an earlier project's tables), creates freqtrade's `ft_main`
   tables from production's catalog (the views need them before any bot
   boots), then recreates every view and function from production's *current*
   definition with its grants. `ft_bot` is created with a **new** password and
   `search_path = ft_main, public`.
2. **Data** -- every local base table production also has, `auth.users` and
   `auth.identities` included, so owner ids, logins and password hashes are
   identical and nobody re-registers. Tables are ordered from the foreign-key
   graph; the `strategy_specs <-> strategy_versions` cycle is broken by
   dropping and re-adding those keys. User triggers on `public` tables are
   paused so the audit trigger cannot mint colliding `security_events` ids;
   `auth.users` is not ours to alter, so its sign-up trigger runs and the
   profiles it invents are deleted before production's are copied. Serial
   *and* identity sequences are advanced past the copied ids.
3. **Adjust** (`db/staging/after_clone.sql`) -- the copied production bot rows
   are retired, the three open positions are deleted (the dry-run bot must not
   "sell" coins it never bought; the 93 closed trades stay as history, still
   attributed to the retired production bot id), and the dashboard's last
   `desired_state` is cleared.
4. **Scrub** -- the copy steps carried passwords in their SQL; those rows in
   `supabase_migrations` are redacted. **Production's database password was
   used for the copy and must be rotated** (Supabase dashboard → Database →
   Reset password), then updated on `freqtrade-bot`'s `SUPABASE_DB_URL`.

Migration `0014_profile_delegates.sql` is not applied here, because it is not
applied to production either; the two schemas are identical.

## How the services were made

`render.staging.yaml` describes the three services. Render blueprint instances
are a dashboard-only feature, so `scripts/render_staging.py` drives the Render
API from that file and refuses anything that is not staging (name, branch,
`ENVIRONMENT`, `DRY_RUN`, forbidden keys):

```
export RENDER_API_KEY=rnd_...
export STAGING_SUPABASE_URL=https://wltovgvpvnsbpzfxtpdu.supabase.co
export STAGING_SUPABASE_ANON_KEY=...          # legacy anon key, Settings -> API Keys
export STAGING_SUPABASE_SERVICE_ROLE_KEY=...  # legacy service_role key, same page
export STAGING_SUPABASE_DB_URL='postgresql://ft_bot.wltovgvpvnsbpzfxtpdu:<password>@aws-0-ap-northeast-1.pooler.supabase.com:5432/postgres'
export STAGING_PLATFORM_OWNER_ID=93fb246c-4bb8-47f6-9b20-c31203b1759f
export STAGING_API_USERNAME=... STAGING_API_PASSWORD=...
export STAGING_ALLOWED_EMAILS=you@example.com
python scripts/render_staging.py --apply            # create what is missing, sync env vars
python scripts/render_staging.py --show             # ids, private hostnames, latest deploy
```

After the bot's first deploy, `--show` prints its private address
(`http://freqtrade-bot-staging-<suffix>:8080` -- the port is `PORT`, not the
10000 Render reports). Set it as `STAGING_FREQTRADE_API_BASE_URL`, run
`--apply` again, then `--deploy` (env changes do not redeploy on their own).

`SUPABASE_JWT_SECRET` is not needed: both projects sign tokens with ES256,
which the app verifies against the project's public keys. The staging pooler
is `aws-0-ap-northeast-1`, not production's `aws-1`; the region is the same
but each project is registered with one pooler.

Still to do by hand in the Supabase dashboard for `wltovgvpvnsbpzfxtpdu`:
close sign-ups (Authentication → Sign In / Providers → Email → disable
sign-ups; `ALLOWED_EMAILS` on the app is the second door), and enable the
IPv4 add-on if the bot should use the direct host like production.

## Working here

- Branch `staging` auto-deploys all three services. Commit there; when a
  change is proven, merge `staging` into `production-2`.
- Everything the production dashboard shows, staging shows for its own bot,
  plus the copied history. Wallet verification reports missing credentials --
  that is correct here.
- `python scripts/render_audit.py` checks both environments at once, including
  the rule that staging never shares a database with production.
- Cost: Render bot (standard) $25 + app $7 + worker $7 + 10 GB disk $2.50;
  Supabase Micro ~$10 (+$4 with IPv4). About $52-56/month.

## Never

- Never copy a production secret to a staging service: not the exchange keys,
  not `SUPABASE_SERVICE_ROLE_KEY`, not `SUPABASE_DB_URL`, not
  `API_USERNAME`/`API_PASSWORD`.
- Never point a staging service at the production Supabase project.
- Never set `DRY_RUN=false` here. The bot refuses anyway; do not find out.
