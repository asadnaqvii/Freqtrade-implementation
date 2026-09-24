"""The verification log stops rewriting the same answer for ever.

Reconciliation re-checks every order it knows about on every run. For an
order that closed last week the answer never changes again, and writing it
afresh each hour is what turned 142 real orders into 46,935 rows on
production -- on the smallest database Supabase sells, which is what
finally exhausted it.

Two properties fix that: a verdict that has not changed is not written
again, and the log has a retention window.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.validation.engine import only_new_verdicts

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = (ROOT / "db" / "migrations" / "0030_trim_the_verification_log.sql").read_text()


class DB:
    """Stands in for the service client; records what it was asked for."""

    def __init__(self, known=None, explode=False):
        self.known = known or []
        self.explode = explode
        self.selects = []

    def select(self, table, *, columns="*", filters=None, order=None, limit=None, offset=None):
        self.selects.append((table, filters, order, limit))
        if self.explode:
            raise RuntimeError("relation is unavailable")
        return list(self.known)


def verdict(pair="TRX/USDT", ft="1", ex="x1", matched=True, kind=None, notes="filled as recorded"):
    return {"pair": pair, "ft_order_id": ft, "exchange_order_id": ex,
            "matched": matched, "discrepancy_kind": kind, "notes": notes}


# -- not rewriting an unchanged verdict -------------------------------------
def test_a_verdict_that_has_not_changed_is_not_written_again():
    db = DB(known=[verdict()])
    assert only_new_verdicts(db, [verdict()], "bot-1") == []


def test_every_repeat_of_a_settled_order_is_skipped():
    """The real shape of the bug: one order, checked hourly for a month."""
    settled = [verdict(ft=str(i), ex="x%d" % i) for i in range(142)]
    db = DB(known=list(settled))
    assert only_new_verdicts(db, settled, "bot-1") == []


def test_a_changed_verdict_is_still_written():
    db = DB(known=[verdict(matched=True, kind=None, notes="filled as recorded")])
    now_disputed = verdict(matched=False, kind="price_mismatch", notes="price differs by 2%")
    assert only_new_verdicts(db, [now_disputed], "bot-1") == [now_disputed]


def test_an_order_never_seen_before_is_written():
    db = DB(known=[verdict(ex="x1")])
    fresh = verdict(ft="2", ex="x2")
    assert only_new_verdicts(db, [verdict(ex="x1"), fresh], "bot-1") == [fresh]


def test_a_verdict_that_flips_back_is_written_again():
    """Agreeing again after a disagreement is a real change, not a repeat."""
    db = DB(known=[verdict(matched=False, kind="price_mismatch", notes="price differs")])
    agrees_again = verdict(matched=True, kind=None, notes="filled as recorded")
    assert only_new_verdicts(db, [agrees_again], "bot-1") == [agrees_again]


def test_the_newest_verdict_is_the_one_compared_against():
    """Rows come back newest first; an older row must not mask the current one."""
    db = DB(known=[
        verdict(matched=False, kind="price_mismatch", notes="price differs"),   # newest
        verdict(matched=True, kind=None, notes="filled as recorded"),           # older
    ])
    assert only_new_verdicts(db, [verdict(matched=False, kind="price_mismatch",
                                          notes="price differs")], "bot-1") == []


def test_orders_are_told_apart_by_pair_and_both_ids():
    db = DB(known=[verdict(pair="TRX/USDT", ft="1", ex="x1")])
    other_pair = verdict(pair="XRP/USDT", ft="1", ex="x1")
    assert only_new_verdicts(db, [other_pair], "bot-1") == [other_pair]


def test_a_finding_with_no_exchange_id_still_dedupes():
    missing = verdict(ex=None, matched=False, kind="missing_on_exchange",
                      notes="The bot recorded this order with no exchange order id.")
    db = DB(known=[missing])
    assert only_new_verdicts(db, [missing], "bot-1") == []


def test_a_failed_read_records_everything_rather_than_losing_a_finding():
    db = DB(explode=True)
    rows = [verdict(), verdict(ft="2", ex="x2")]
    assert only_new_verdicts(db, rows, "bot-1") == rows


def test_nothing_to_write_asks_the_database_nothing():
    db = DB()
    assert only_new_verdicts(db, [], "bot-1") == []
    assert db.selects == []


def test_the_lookback_is_scoped_to_this_bot_and_newest_first():
    db = DB(known=[])
    only_new_verdicts(db, [verdict()], "bot-1")
    [(table, filters, order, limit)] = db.selects
    assert table == "order_reconciliations"
    assert filters == {"bot_instance_id": "eq.bot-1"}
    assert order == "checked_at.desc"
    assert limit >= 1000


# -- the reconciliation only looks as far back as the venue can answer -------
def test_reconciliation_only_asks_for_orders_inside_the_venue_window():
    source = (ROOT / "app" / "validation" / "selfcheck.py").read_text()
    reconcile = source[source.index("def reconcile("):]
    assert 'filters={"order_date": f"gte.{since}"}' in reconcile
    assert "timedelta(days=lookback_days)" in reconcile


# -- retention ---------------------------------------------------------------
def test_the_prune_function_exists_and_only_the_service_role_may_run_it():
    assert "create or replace function public.prune_validation_records" in MIGRATION
    assert "revoke all on function public.prune_validation_records(int, int) from public, anon, authenticated;" in MIGRATION
    assert "grant execute on function public.prune_validation_records(int, int) to service_role;" in MIGRATION
    assert "security definer" in MIGRATION


def test_the_prune_is_bounded_so_a_backlog_cannot_lock_the_table():
    assert "limit greatest(p_limit, 1)" in MIGRATION
    assert "make_interval(days => greatest(p_keep_days, 1))" in MIGRATION


def test_deleting_a_run_is_what_trims_its_children():
    """validation_checks and order_reconciliations cascade from validation_runs."""
    schema = (ROOT / "db" / "migrations" / "0005_validation.sql").read_text()
    for table in ("validation_checks", "order_reconciliations"):
        block = schema[schema.index("create table if not exists public.%s" % table):]
        block = block[:block.index(");")]
        assert re.search(r"run_id\s+uuid[^,]*references public\.validation_runs \(id\) on delete cascade", block), table
    assert "delete from public.validation_runs" in MIGRATION


def test_the_cleanup_keeps_the_first_of_each_run_of_identical_verdicts():
    assert "row_number() over w" in MIGRATION
    assert "r.rn > 1" in MIGRATION
    for column in ("matched", "discrepancy_kind", "notes"):
        assert "r.%s            is not distinct from r.prev_" % column in MIGRATION \
            or "r.%s          is not distinct from r.prev_" % column in MIGRATION \
            or "r.%s is not distinct from r.prev_" % column in MIGRATION, column
    assert "partition by bot_instance_id, pair" in MIGRATION


def test_the_worker_prunes_the_verification_log_on_its_schedule():
    worker = (ROOT / "app" / "worker" / "main.py").read_text()
    assert "VALIDATION_KEEP_DAYS = 90" in worker
    assert 'client.rpc("prune_validation_records"' in worker
    assert worker.count("prune_validation()") >= 2      # at boot and on the timer


def test_the_prune_reports_how_many_it_removed():
    """The worker logs this number. prune_security_events hands back 1 whatever
    it deleted, and null when it deleted nothing; this one counts."""
    assert "select coalesce(count(*), 0)::int from removed" in MIGRATION
    assert "returning 1\n  )" in MIGRATION


def test_the_migration_is_listed_for_whoever_applies_them():
    readme = (ROOT / "db" / "migrations" / "README.md").read_text()
    assert "0030_trim_the_verification_log.sql" in readme
