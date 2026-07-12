#!/usr/bin/env python3
"""Export all trade data and statistics from a running Freqtrade bot.

Two modes:

1. REST API mode (works against a deployed bot, e.g. on Railway):
       python scripts/export_trade_data.py --url https://your-app.up.railway.app \
           --username freqtrader --password <password>

   Credentials can also come from env vars FT_URL, FT_USERNAME, FT_PASSWORD.

2. SQLite mode (works against a local/downloaded database file):
       python scripts/export_trade_data.py --db user_data/tradesv3.sqlite

Everything is written to an output directory (default: trade_export/):
  - trades.json / trades.csv   -> every past trade with full details
  - profit.json                -> overall profit summary
  - performance.json           -> per-pair performance
  - daily.json / weekly.json / monthly.json -> timeframed profit stats
  - stats.json                 -> win/loss streaks, holding durations
  - balance.json, status.json, show_config.json -> current bot state
"""

import argparse
import csv
import json
import os
import sqlite3
import sys
from pathlib import Path

try:
    import requests
except ImportError:
    requests = None


API_ENDPOINTS = {
    "profit": "profit",
    "performance": "performance",
    "daily": "daily?timescale=365",
    "weekly": "weekly?timescale=104",
    "monthly": "monthly?timescale=36",
    "stats": "stats",
    "balance": "balance",
    "status": "status",
    "show_config": "show_config",
    "count": "count",
    "locks": "locks",
    "whitelist": "whitelist",
}


def login(session, base_url, username, password):
    resp = session.post(
        f"{base_url}/api/v1/token/login", auth=(username, password), timeout=30
    )
    resp.raise_for_status()
    token = resp.json()["access_token"]
    session.headers["Authorization"] = f"Bearer {token}"


def fetch_all_trades(session, base_url):
    """Page through /trades until every closed trade is retrieved."""
    trades, offset, limit = [], 0, 500
    while True:
        resp = session.get(
            f"{base_url}/api/v1/trades", params={"limit": limit, "offset": offset},
            timeout=60,
        )
        resp.raise_for_status()
        data = resp.json()
        batch = data.get("trades", [])
        trades.extend(batch)
        total = data.get("total_trades", len(trades))
        offset += len(batch)
        if not batch or offset >= total:
            return trades


def export_via_api(base_url, username, password, outdir):
    if requests is None:
        sys.exit("The 'requests' package is required for API mode: pip install requests")
    base_url = base_url.rstrip("/")
    session = requests.Session()
    login(session, base_url, username, password)
    print(f"Logged in to {base_url}")

    trades = fetch_all_trades(session, base_url)
    write_json(outdir / "trades.json", trades)
    write_trades_csv(outdir / "trades.csv", trades)
    print(f"Exported {len(trades)} closed trades")

    for name, endpoint in API_ENDPOINTS.items():
        try:
            resp = session.get(f"{base_url}/api/v1/{endpoint}", timeout=60)
            resp.raise_for_status()
            write_json(outdir / f"{name}.json", resp.json())
            print(f"Exported {name}")
        except Exception as exc:  # keep going; some endpoints need a running bot
            print(f"Skipped {name}: {exc}")


def export_via_sqlite(db_path, outdir):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    for table in ("trades", "orders"):
        try:
            rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table}")]
        except sqlite3.OperationalError as exc:
            print(f"Skipped table {table}: {exc}")
            continue
        write_json(outdir / f"{table}.json", rows)
        if rows:
            with open(outdir / f"{table}.csv", "w", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
        print(f"Exported {len(rows)} rows from '{table}'")
    conn.close()


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, default=str))


def write_trades_csv(path, trades):
    if not trades:
        return
    fields = sorted({k for t in trades for k in t})
    with open(path, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for t in trades:
            writer.writerow({k: t.get(k, "") for k in fields})


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=os.environ.get("FT_URL"),
                        help="Base URL of the bot API, e.g. https://app.up.railway.app")
    parser.add_argument("--username", default=os.environ.get("FT_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("FT_PASSWORD"))
    parser.add_argument("--db", help="Path to tradesv3.sqlite for offline export")
    parser.add_argument("--outdir", default="trade_export")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    if args.db:
        export_via_sqlite(args.db, outdir)
    elif args.url and args.username and args.password:
        export_via_api(args.url, args.username, args.password, outdir)
    else:
        parser.error("Provide either --db PATH, or --url/--username/--password "
                     "(or FT_URL/FT_USERNAME/FT_PASSWORD env vars)")

    print(f"\nDone. Files written to {outdir.resolve()}")


if __name__ == "__main__":
    main()
