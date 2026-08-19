# Moving the existing trade history into Supabase

Freqtrade writes to a SQLite file inside its container. On Render and Railway
that filesystem does not survive a redeploy, so the history the dashboard shows
today exists in exactly one place and disappears the next time the service
restarts.

This moves it into Postgres, where it persists. **Run it with the bot stopped,
before pointing anything at Postgres.**

## What the script does

`scripts/migrate_trades_to_supabase.py`:

1. Counts rows in the source SQLite.
2. Counts rows already in the target, and warns if there are any — `convert-db`
   appends, so running the migration twice would duplicate history.
3. Copies with freqtrade's own `convert-db`, which understands freqtrade's schema
   and its migrations.
4. Counts again and **refuses to report success if the numbers do not add up.**
5. Builds the live views and backfills `public.trade_archive`.

Step 4 is the point. During development `convert-db` exited successfully having
copied nothing, and only the recount caught it.

## Running it

The live database is on the host running the bot, so run this there — a Railway
shell, or wherever the bot currently runs.

```bash
# See what would move; writes nothing.
python scripts/migrate_trades_to_supabase.py \
    --source sqlite:///user_data/tradesv3.sqlite \
    --target "postgresql://postgres.<ref>:<password>@aws-0-<region>.pooler.supabase.com:5432/postgres" \
    --dry-run

# Do it.
python scripts/migrate_trades_to_supabase.py \
    --source sqlite:///user_data/tradesv3.sqlite \
    --target "$SUPABASE_DB_URL"
```

Freqtrade's default file is `user_data/tradesv3.sqlite` for live runs and
`user_data/tradesv3.dryrun.sqlite` for dry runs. If you are not sure which one is
live, check the size and modification time — the live one is the one changing.

## Afterwards

The source file is never modified. **Keep it** until you have seen the history in
the dashboard and are satisfied.

Then set `SUPABASE_DB_URL` on the bot service and redeploy. On the next boot the
log reads:

```
persistence: postgres, schema ft_main
```

rather than

```
persistence: SQLite (ephemeral -- set SUPABASE_DB_URL to keep history)
```

## If the counts do not reconcile

The script stops and leaves the source untouched. The usual causes:

- **Target already had rows.** The script warns before starting; if you continued
  past it, the target now holds both sets. Clear the `ft_main` schema and rerun.
- **Connection dropped mid-copy.** `convert-db` is not transactional across
  tables. Clear the `ft_main` schema and rerun.
- **Wrong source file.** Dry-run against both SQLite files and compare.

To clear the schema and start over:

```sql
drop schema ft_main cascade;
create schema ft_main;
```

That destroys only freqtrade's copy. Anything already in `public.trade_archive`
survives, which is the reason the archive exists.
