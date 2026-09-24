#!/usr/bin/env python3
"""Check whether this host can actually trade on a given exchange.

Run it from wherever the bot will run -- a Render shell, a Railway shell, your
laptop -- before wiring anything else up. It needs no database and no platform
account; it reads credentials from the environment and talks to the venue.

The question it exists to answer quickly: is this host in a region the exchange
will serve? KuCoin blocks US IP addresses, Render defaults to Oregon, and a
Render service's region cannot be changed after it is created -- so finding this
out before building the rest of the deployment saves rebuilding it.

    python scripts/preflight.py
    python scripts/preflight.py --exchange binance --pairs BTC/USDT ETH/USDT
    python scripts/preflight.py --kind connectivity      # skip the account checks

Exit code is 0 when nothing failed, 1 otherwise, so it can gate a deploy.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import registry  # noqa: E402
from app.providers.base import Credentials, ProviderError  # noqa: E402
from app.validation import checks as C  # noqa: E402
from app.validation import engine  # noqa: E402

# Rendered without colour when stdout is not a terminal, so piping to a log file
# does not fill it with escape codes.
_TTY = sys.stdout.isatty()


def paint(text: str, colour: str) -> str:
    if not _TTY:
        return text
    codes = {"green": "32", "red": "31", "yellow": "33", "grey": "90", "bold": "1"}
    return f"\033[{codes.get(colour, '0')}m{text}\033[0m"


BADGE = {
    C.PASSED: ("PASS", "green"),
    C.FAILED: ("FAIL", "red"),
    C.ERROR: ("ERR ", "red"),
    C.WARNING: ("WARN", "yellow"),
    C.SKIPPED: ("SKIP", "grey"),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--exchange", default=os.environ.get("FREQTRADE__EXCHANGE__NAME", "kucoin"))
    parser.add_argument("--kind", default="preflight",
                        choices=sorted(C.SUITES), help="which suite to run")
    parser.add_argument("--pairs", nargs="*", default=["BTC/USDT", "ETH/USDT"])
    parser.add_argument("--stake-currency", default=os.environ.get("FREQTRADE_STAKE_CURRENCY", "USDT"))
    parser.add_argument("--stake-amount", type=float,
                        default=float(os.environ.get("FREQTRADE_STAKE_AMOUNT", "10")))
    parser.add_argument("--max-open-trades", type=int,
                        default=int(os.environ.get("FREQTRADE_MAX_OPEN_TRADES", "6")))
    parser.add_argument("--sandbox", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s %(message)s",
    )

    credentials = Credentials(
        key=os.environ.get("FREQTRADE__EXCHANGE__KEY"),
        secret=os.environ.get("FREQTRADE__EXCHANGE__SECRET"),
        password=os.environ.get("FREQTRADE__EXCHANGE__PASSWORD"),
    )

    print()
    print(paint(f"Preflight: {args.exchange}", "bold"))
    print(f"  suite       {args.kind}")
    print(f"  credentials {'present' if credentials.present else paint('absent', 'yellow')}")
    print(f"  pairs       {', '.join(args.pairs) or 'none'}")
    print()

    account = {
        "provider": args.exchange,
        "ccxt_id": args.exchange,
        "is_sandbox": args.sandbox,
        "label": "preflight",
    }

    try:
        provider = registry.build(account, credentials=credentials)
    except ProviderError as exc:
        print(paint(f"Cannot build a client for {args.exchange}: {exc}", "red"))
        return 1

    try:
        outcome = engine.run_suite(
            args.kind, provider,
            pairs=args.pairs,
            stake_currency=args.stake_currency,
            stake_amount=args.stake_amount,
            max_open_trades=args.max_open_trades,
        )
    finally:
        provider.close()

    for result in outcome.results:
        label, colour = BADGE.get(result.status, ("????", "grey"))
        print(f"  {paint(label, colour)}  {result.title}")
        if result.message:
            print(f"        {paint(result.message, 'grey')}")
        if result.remediation and result.status in (C.FAILED, C.ERROR, C.WARNING):
            for index, line in enumerate(_wrap(result.remediation, 74)):
                prefix = "-> " if index == 0 else "   "
                print(f"        {paint(prefix + line, 'yellow')}")

    counts = outcome.counts
    print()
    print(f"  egress   {outcome.egress_ip or 'unknown'} "
          f"({outcome.egress_region or 'region unknown'})")
    print(f"  checks   {counts['passed']} passed, {counts['warning']} warned, "
          f"{counts['failed']} failed")
    print()

    if outcome.status == "failed":
        print(paint(f"  FAILED -- {outcome.summary}", "red"))
    elif outcome.status == "warning":
        print(paint(f"  PASSED WITH WARNINGS -- {outcome.summary}", "yellow"))
    else:
        print(paint(f"  OK -- {outcome.summary}", "green"))

    # The single most useful line for the Render/KuCoin question.
    region = (outcome.egress_region or "").upper()
    if region:
        if region in C.BLOCKED_EGRESS:
            print()
            print(paint(f"  This host egresses from {region}. KuCoin will refuse it.", "red"))
            print("  Rebuild the service in a non-US region (singapore or frankfurt).")
        else:
            print()
            print(paint(f"  Egress region {region} is fine for KuCoin.", "green"))
    print()

    return 1 if outcome.status == "failed" else 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    lines: list[str] = []
    for paragraph in text.splitlines():
        lines.extend(textwrap.wrap(paragraph, width) or [""])
    return lines


if __name__ == "__main__":
    sys.exit(main())
