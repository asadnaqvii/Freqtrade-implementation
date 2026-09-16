# Migrations

Applied by hand, in filename order, through the Supabase SQL editor or the MCP
`apply_migration` tool. There is no runner and nothing records which have run,
so this list is the record. Every file is idempotent and safe to re-apply.

| File | Apply to a fresh project? | Note |
|---|---|---|
| `0001_foundation.sql` … `0008_views_and_catalog.sql` | yes | 0008 seeds `indicator_catalog` with `on conflict do update`, so it can be re-run |
| `0009_secure_legacy_tables.sql` | **no** | RLS for five tables left by an earlier project. They do not exist in a fresh database, and the unguarded `alter table`s fail. Production only |
| `0010_harden.sql` … `0013_fix_audit_trigger.sql` | yes | |
| `0014_profile_delegates.sql` | **not yet** | Written, never applied to production. Applying it anywhere else makes `current_profile_id()` differ between environments; apply it to production first, then everywhere |
| `0015_backtest_coverage.sql` | yes | Two files share the number 0015. This one was written first (2026-08-19) |
| `0015_service_role_reads_ft_schema.sql` | yes | The second 0015 (2026-08-20). Order between the two does not matter; both are independent |
| `0016_name_the_oom.sql` … `0025_expose_started_at.sql` | yes | |

`ft_main` -- freqtrade's own tables -- is created by freqtrade on its first
connect, not by any file here. A copy of an existing database has to bring
those tables with it (`db/staging/`).
