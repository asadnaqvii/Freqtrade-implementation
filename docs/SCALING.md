# Scaling beyond one user

This deployment serves one operator. The schema and the code are shaped so that
adding users later is additive rather than a rewrite, but none of the machinery
for it is built, because building it now would be paying for a problem nobody
has.

This document says where the seams are.

## What already works for many users

| Seam | Today | To scale |
|---|---|---|
| `owner_id` on every table, RLS enforced | one profile | nothing — the policies are written and tested |
| `bot_instances` is one row per bot | one row | insert a row |
| Freqtrade's schema name is a column, not a constant | `ft_main` | `ft_<bot id>` per bot |
| Credentials resolve by env-var **name** | reads `os.environ` | change one function |
| Backtest queue uses `FOR UPDATE SKIP LOCKED` | one worker | run more workers |
| Per-account quotas in `profiles` | enforced by trigger | raise the numbers |

RLS is verified, not assumed: two profiles, a strategy and a wallet each, and a
check that user A can neither read nor forge user B's rows.

## The part that genuinely does not scale as-is

Freqtrade is single-tenant by construction. One process is one exchange account,
one config, one strategy, one pairlist, and its `trades` table has no user
column. There is no version of "one freqtrade serving many users".

So multi-user live trading means **a process per user**, and that is the work:

1. **A provisioner.** Something that creates, starts, stops and destroys a
   container per bot. `bot_instances` becomes desired state — add
   `desired_state`, `actual_state`, `runtime_id` — and a reconciler loop drives
   actual towards desired. That shape is crash-safe and idempotent, which matters
   when the thing being reconciled places real orders.

2. **Credential storage.** Env vars stop working once keys belong to users
   rather than to the deployment. Replace `app/providers/credentials.py:resolve`
   with envelope encryption: a per-account data key wrapped by a master key held
   outside the database, AES-GCM, with the account id as additional
   authenticated data so a ciphertext cannot be moved between accounts. The
   plaintext should only ever exist inside the user's own container, injected at
   provision time.

   The database already refuses to store anything that is not env-var-shaped, so
   this migration cannot be half-done without failing loudly.

3. **Per-bot database isolation.** Give each bot its own schema *and its own
   Postgres role*, with `ALTER ROLE ft_<id> SET search_path = ft_<id>, public`
   and grants limited to that schema. Role-level `search_path` survives
   connection pooling, which the `options=-c search_path=...` approach does not
   reliably do through a transaction pooler. A leaked bot credential then reaches
   one tenant's data rather than all of it.

4. **Connection budget.** Each freqtrade process holds a SQLAlchemy pool.
   Supabase's connection limit, not CPU or RAM, is what breaks first — expect to
   hit it around 20 bots on a small instance. Route bots through the transaction
   pooler and keep pools small.

## What it costs

An idle freqtrade process is roughly 150–250 MB of RSS and almost no CPU between
candles. An 8 GB box holds 25–30 of them, so per-bot compute lands near
$1.50/month on a shared host. Fly.io Machines suit this better than Render:
they start in under a second, bill per second, and scale to zero, whereas Render
charges a full service per bot with no scale-to-zero.

Backtest workers do not scale per user at all — two or three shared workers serve
hundreds of people, because a backtest is minutes of work rather than a
long-running process. Verification is a handful of stateless API calls and costs
nothing.

The honest summary: **the expensive part is live trading, and only live
trading.** Strategy authoring, backtesting and verification are already
multi-tenant and nearly free.

## Two operational constraints that surprise people

- **Egress IP.** Users who bind an exchange key to an IP address need every bot
  to leave from a stable one. Container platforms give you dynamic egress by
  default; you need a NAT gateway or dedicated address.
- **Region.** Every bot touching KuCoin must egress from outside the US, so the
  provisioner's placement is constrained, not free.
