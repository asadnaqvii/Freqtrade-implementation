#!/usr/bin/env python3
"""Copy freqtrade's trade history from SQLite into Supabase Postgres.

Run this once, with the bot stopped, before pointing it at Postgres. It uses
freqtrade's own `convert-db`, which understands freqtrade's schema and its
migrations; this script's job is to make that safe -- count the rows first,
count them again afterwards, and refuse to claim success on a mismatch.

Typical use, from a shell on the host that has the live SQLite file:

    python scripts/migrate_trades_to_supabase.py \\
        --source sqlite:///user_data/tradesv3.sqlite \\
        --target "$SUPABASE_DB_URL"

Add --dry-run to see what would move without writing anything.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import ConfigError, with_search_path  # noqa: E402

# The tables freqtrade owns. keyvaluestore and pairlocks matter less than trades
# and orders, but losing them silently changes bot behaviour after a restart.
FREQTRADE_TABLES = ["trades", "orders", "pairlocks", "keyvaluestore"]


def count_rows(db_url: str, tables: list[str]) -> dict[str, int]:
    """Count rows per table, tolerating tables that do not exist."""
    from sqlalchemy import create_engine, inspect, text

    engine = create_engine(db_url)
    counts: dict[str, int] = {}
    try:
        inspector = inspect(engine)
        present = set(inspector.get_table_names())
        with engine.connect() as connection:
            for table in tables:
                if table not in present:
                    counts[table] = -1  # absent, as opposed to empty
                    continue
                counts[table] = connection.execute(
                    text(f"select count(*) from {table}")  # noqa: S608 - fixed list
                ).scalar_one()
    finally:
        engine.dispose()
    return counts


def describe(counts: dict[str, int]) -> str:
    parts = []
    for table, count in counts.items():
        parts.append(f"{table}={'absent' if count < 0 else count}")
    return ", ".join(parts)


def run_convert(source: str, target: str) -> None:
    # convert-db refuses to start unless ./user_data exists, even though it
    # reads nothing from it, and it does not accept --userdir. Run it from a
    # scratch directory that has one rather than making the operator run
    # `freqtrade create-userdir` first or littering the repo.
    import tempfile

    workdir = Path(tempfile.mkdtemp(prefix="ft-convert-"))
    for sub in ("data", "logs", "strategies", "notebooks", "hyperopt_results", "backtest_results"):
        (workdir / "user_data" / sub).mkdir(parents=True, exist_ok=True)

    cmd = [sys.executable, "-m", "freqtrade", "convert-db",
           "--db-url-from", source, "--db-url", target]
    # Never print the target: it carries the database password.
    print(f"  running freqtrade convert-db from {source} -> <target>", flush=True)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=workdir)
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    if result.returncode != 0:
        tail = "\n".join((result.stdout + result.stderr).strip().splitlines()[-25:])
        raise SystemExit(f"convert-db failed:\n{tail}")
    print("  convert-db finished", flush=True)


def backfill_archive(schema: str) -> None:
    """Copy closed trades into public.trade_archive.

    The archive is ours, not freqtrade's. Keeping a copy means the history
    survives a freqtrade schema reset, a switch of exchange, or a future version
    that migrates its tables in a way we did not expect.
    """
    try:
        from app.core.supabase import SupabaseClient
    except Exception as exc:
        print(f"  skipping archive backfill: {exc}")
        return

    try:
        client = SupabaseClient.service()
        result = client.rpc("refresh_freqtrade_views", {"p_schema": schema})
        print(f"  live views: {result}")
    except Exception as exc:
        print(f"  could not refresh live views: {exc}")
        return

    try:
        bot = client.select_one("bot_instances", columns="id,owner_id,exchange",
                                filters={"db_schema": f"eq.{schema}"})
        trades = client.select("v_live_trades", limit=5000)
    except Exception as exc:
        print(f"  could not read migrated trades: {exc}")
        return

    if not trades:
        print("  no trades to archive")
        return

    rows = []
    for trade in trades:
        rows.append({
            "bot_instance_id": bot["id"] if bot else None,
            "owner_id": bot.get("owner_id") if bot else None,
            "ft_trade_id": trade["ft_trade_id"],
            "pair": trade["pair"],
            "base_currency": trade.get("base_currency"),
            "quote_currency": trade.get("quote_currency"),
            "exchange": trade.get("exchange"),
            "strategy": trade.get("strategy"),
            "timeframe": trade.get("timeframe"),
            "is_open": bool(trade.get("is_open")),
            "is_short": bool(trade.get("is_short")),
            "amount": trade.get("amount"),
            "stake_amount": trade.get("stake_amount"),
            "open_rate": trade.get("open_rate"),
            "close_rate": trade.get("close_rate"),
            "open_date": trade.get("open_date"),
            "close_date": trade.get("close_date"),
            "close_profit_abs": trade.get("close_profit_abs"),
            "close_profit_pct": trade.get("close_profit_pct"),
            "realized_profit": trade.get("realized_profit"),
            "fee_open": trade.get("fee_open"),
            "fee_close": trade.get("fee_close"),
            "enter_tag": trade.get("enter_tag"),
            "exit_reason": trade.get("exit_reason"),
            "leverage": trade.get("leverage"),
        })

    if bot is None:
        print("  no bot_instances row for this schema; archiving without a bot link")

    try:
        written = 0
        for start in range(0, len(rows), 200):
            chunk = rows[start:start + 200]
            client.upsert("trade_archive", chunk,
                          on_conflict="bot_instance_id,ft_trade_id", returning=False)
            written += len(chunk)
        print(f"  archived {written} trade(s)")
    except Exception as exc:
        print(f"  archive backfill failed: {exc}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", default="sqlite:///user_data/tradesv3.sqlite",
                        help="source database (default: freqtrade's live-run SQLite)")
    parser.add_argument("--target", default=os.environ.get("SUPABASE_DB_URL"),
                        help="target Postgres URL (default: $SUPABASE_DB_URL)")
    parser.add_argument("--schema", default=os.environ.get("FREQTRADE_DB_SCHEMA", "ft_main"),
                        help="schema on the target holding freqtrade's tables")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would move, write nothing")
    parser.add_argument("--skip-archive", action="store_true",
                        help="do not backfill public.trade_archive afterwards")
    args = parser.parse_args()

    if not args.target:
        return _fail("no target given. Pass --target or set SUPABASE_DB_URL.")

    if args.source.startswith("sqlite:///"):
        path = Path(args.source.replace("sqlite:///", "", 1))
        if not path.exists():
            return _fail(
                f"source database {path} does not exist.\n"
                "Run this on the host holding the live bot's file. Freqtrade's default is "
                "user_data/tradesv3.sqlite for live and tradesv3.dryrun.sqlite for dry runs."
            )

    try:
        target = with_search_path(args.target, args.schema)
    except ConfigError as exc:
        return _fail(str(exc))

    print("1. reading the source")
    try:
        before = count_rows(args.source, FREQTRADE_TABLES)
    except Exception as exc:
        return _fail(f"could not read the source database: {exc}")
    print(f"   {describe(before)}")

    movable = {t: c for t, c in before.items() if c > 0}
    if not movable:
        print("\nNothing to migrate: the source has no rows in any freqtrade table.")
        return 0

    print("\n2. reading the target")
    try:
        target_before = count_rows(target, FREQTRADE_TABLES)
        print(f"   {describe(target_before)}")
    except Exception as exc:
        return _fail(
            f"could not reach the target database: {exc}\n"
            "If the host is db.<ref>.supabase.co, use the session pooler URI instead -- "
            "the direct host resolves to IPv6 only."
        )

    existing = sum(c for c in target_before.values() if c > 0)
    if existing:
        print(f"\n   The target already holds {existing} row(s). convert-db appends, so "
              "running this twice would duplicate them.")
        if not args.dry_run and input("   Continue anyway? [y/N] ").strip().lower() != "y":
            print("   Stopped.")
            return 1

    if args.dry_run:
        print("\nDry run: would copy " + describe(movable))
        return 0

    print("\n3. copying")
    # The copy runs from a scratch directory, so a relative sqlite path in the
    # source would resolve against the wrong place. Make it absolute first.
    source = args.source
    if source.startswith("sqlite:///") and not source.startswith("sqlite:////"):
        source = "sqlite:///" + str(Path(source[len("sqlite:///"):]).resolve())
    run_convert(source, target)

    print("\n4. verifying")
    after = count_rows(target, FREQTRADE_TABLES)
    print(f"   {describe(after)}")

    problems = []
    for table, source_count in before.items():
        if source_count <= 0:
            continue
        expected = source_count + max(target_before.get(table, 0), 0)
        actual = after.get(table, -1)
        if actual < expected:
            problems.append(f"{table}: expected at least {expected}, found {actual}")

    if problems:
        return _fail(
            "row counts do not add up after the copy:\n  " + "\n  ".join(problems) +
            "\n\nThe source database has not been touched. Investigate before "
            "pointing the bot at the target."
        )

    print("   counts reconcile")

    if not args.skip_archive:
        print("\n5. archiving into public.trade_archive")
        backfill_archive(args.schema)

    print(
        "\nDone. The source database is untouched -- keep it until you have seen the "
        "history in the dashboard.\n"
        "Next: set SUPABASE_DB_URL on the bot service and redeploy."
    )
    return 0


def _fail(message: str) -> int:
    print(f"\nERROR: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
