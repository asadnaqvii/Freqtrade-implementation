"""What the Learning Module's migrations promise, read from their SQL.

Text assertions, no database: what they hold is that no role can change or
remove evidence, that the only deletion path is the prune function, that
readers only see their own rows, and that both files can be applied twice.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS = Path(__file__).resolve().parents[1] / "db" / "migrations"
DECISIONS = (MIGRATIONS / "0027_trading_decisions.sql").read_text()
HEALTH = (MIGRATIONS / "0028_learning_health.sql").read_text()
EVIDENCE_TABLES = ("public.trading_decisions", "public.trading_events")


def without_comments(sql: str) -> str:
    return "\n".join(line.split("--")[0] for line in sql.splitlines())


def grants(sql: str) -> list[tuple[set[str], set[str], set[str]]]:
    """(privileges, objects, roles) for every table or view grant."""
    found = []
    pattern = r"grant\s+(.+?)\s+on\s+(?!function\b)(?:table\s+)?(.+?)\s+to\s+(.+?);"
    for match in re.finditer(pattern, without_comments(sql), re.S | re.I):
        privileges, objects, roles = (part.strip().lower() for part in match.groups())
        found.append((
            {p.strip() for p in privileges.split(",")},
            {o.strip() for o in objects.split(",")},
            {r.strip() for r in roles.split(",")},
        ))
    return found


def test_no_role_may_update_or_delete_evidence():
    seen = 0
    for privileges, objects, roles in grants(DECISIONS) + grants(HEALTH):
        if objects & set(EVIDENCE_TABLES):
            seen += 1
            assert privileges <= {"select", "insert"}, (privileges, objects, roles)
    assert seen >= 2


def test_the_evidence_tables_are_taken_away_from_anon_and_writes_from_readers():
    for table in EVIDENCE_TABLES:
        assert f"revoke all on {table} from anon;" in DECISIONS
        assert re.search(rf"revoke insert, update, delete.*on {re.escape(table)} from authenticated;", DECISIONS)
        assert re.search(rf"revoke update, delete.*on {re.escape(table)} from service_role;", DECISIONS)


def test_every_learning_table_forces_row_level_security():
    for table, sql in ((EVIDENCE_TABLES[0], DECISIONS), (EVIDENCE_TABLES[1], DECISIONS),
                       ("public.learning_writer_status", HEALTH)):
        assert f"alter table {table} enable row level security;" in sql
        assert f"alter table {table} force row level security;" in sql


def test_readers_only_see_their_own_rows_through_the_cached_form_of_auth_uid():
    for sql in (DECISIONS, HEALTH):
        body = without_comments(sql)
        policies = re.findall(r"create policy \w+ on (\S+)\s+for select to authenticated\s+using \((.+?)\);", body)
        assert policies, "every table needs an owner-read policy"
        for _table, predicate in policies:
            assert predicate == "owner_id = (select auth.uid())"
        assert "auth.uid()" not in body.replace("(select auth.uid())", "")


def test_a_trigger_refuses_updates_and_deletes_on_both_evidence_tables():
    for table in EVIDENCE_TABLES:
        assert re.search(
            rf"before update or delete on {re.escape(table)}\s+for each row execute function "
            r"public\.learning_is_append_only\(\)",
            DECISIONS,
        )
    assert "raise exception 'learning records are append-only" in DECISIONS


def test_pruning_is_the_only_delete_path_and_only_the_service_role_may_run_it():
    body = without_comments(DECISIONS)
    start = body.index("create or replace function public.prune_learning_records")
    opening = body.index("$$", start)
    closing = body.index("$$;", opening + 2)
    deletes = [m.start() for m in re.finditer(r"\bdelete from\b", body)]
    assert len(deletes) == 2
    assert all(opening < position < closing for position in deletes)
    assert "revoke all on function public.prune_learning_records(integer) from public, anon, authenticated;" in body
    assert "grant execute on function public.prune_learning_records(integer) to service_role;" in body


def test_the_prune_flag_is_transaction_local_and_the_trigger_checks_it():
    assert "set_config('learning.allow_prune', 'on', true)" in DECISIONS
    assert "current_setting('learning.allow_prune', true) = 'on' and tg_op = 'DELETE'" in DECISIONS


def test_an_event_does_not_carry_a_foreign_key_to_its_decision():
    events = DECISIONS[DECISIONS.index("create table if not exists public.trading_events"):]
    line = next(l for l in events.splitlines() if l.strip().startswith("decision_id"))
    assert "references" not in line
    assert "not null" not in line


def test_every_record_is_unique_on_its_idempotency_key():
    assert DECISIONS.count("idempotency_key      text not null unique") == 1
    assert DECISIONS.count("idempotency_key    text not null unique") == 1


def test_both_files_can_be_applied_twice():
    for sql in (DECISIONS, HEALTH):
        body = without_comments(sql)
        assert not re.search(r"create table (?!if not exists)", body)
        assert not re.search(r"create index (?!if not exists)", body)
        assert not re.search(r"create (?!or replace )(function|view)", body)
        for name in re.findall(r"create policy (\w+)", body):
            assert f"drop policy if exists {name}" in body
        for name in re.findall(r"create trigger (\w+)", body):
            assert f"drop trigger if exists {name}" in body
    for enum in ("decision_kind", "rejection_stage"):
        assert f"if not exists (select 1 from pg_type where typname = '{enum}')" in DECISIONS


def test_the_health_view_runs_as_the_reader_and_counts_what_did_not_arrive():
    assert "create or replace view public.v_learning_health\nwith (security_invoker = on)" in HEALTH
    for column in ("decisions_24h", "quarantined_24h", "events_24h", "events_without_decision_24h",
                   "decisions_without_events_24h", "last_decision_at"):
        assert column in HEALTH


def test_the_status_row_the_writer_publishes_fits_the_status_table():
    from app.learning.status import status_row

    row = status_row({"outbox_pending": 3, "outages_total": 1}, bot_instance_id="b",
                     owner_id=None, adapter_version="1.0")
    table = HEALTH[HEALTH.index("create table if not exists public.learning_writer_status"):]
    table = table[:table.index(");")]
    columns = set(re.findall(r"^\s{2}(\w+)\s", table, re.M))
    assert set(row) <= columns, set(row) - columns
    assert row["outages_total"] == 1


def test_the_migrations_are_listed_in_the_readme():
    readme = (MIGRATIONS / "README.md").read_text()
    assert "0027_trading_decisions.sql" in readme
    assert "0028_learning_health.sql" in readme


def test_the_health_view_is_read_only_for_everyone():
    body = without_comments(HEALTH)
    revoke = "revoke all on public.v_learning_health from anon, authenticated, service_role;"
    assert revoke in body
    assert body.index(revoke) < body.index("grant select on public.v_learning_health")
    for privileges, objects, _roles in grants(HEALTH):
        if "public.v_learning_health" in objects:
            assert privileges == {"select"}
