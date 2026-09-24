#!/usr/bin/env python3
"""Audit a Render account for the things that break KuCoin trading.

Read-only. It creates, modifies and deletes nothing -- it lists services and
tells you which ones cannot work, and why.

It exists because the single most expensive mistake in this deployment is
invisible from the dashboard: a service's region. KuCoin refuses US IP
addresses, Render defaults to Oregon, and a service's region cannot be changed
after it is created. A service in the wrong region has to be replaced, so it is
worth knowing before you build anything else on top of it.

    export RENDER_API_KEY=rnd_...
    python scripts/render_audit.py

Add --env to also report which environment variables each service defines.
Only names are printed, never values.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

API = "https://api.render.com/v1"

# Render regions that sit in the United States. KuCoin blocks all of them.
US_REGIONS = {"oregon", "ohio", "virginia"}
GOOD_REGIONS = {"singapore", "frankfurt"}

# Variables this deployment needs, per service role.
EXPECTED = {
    "bot": [
        "FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET",
        "FREQTRADE__EXCHANGE__PASSWORD", "SUPABASE_DB_URL",
    ],
    "app": [
        "SUPABASE_URL", "SUPABASE_ANON_KEY", "SUPABASE_SERVICE_ROLE_KEY",
        "SUPABASE_JWT_SECRET",
    ],
    "worker": ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"],
}


class RenderError(RuntimeError):
    pass


def call(path: str, token: str) -> object:
    request = urllib.request.Request(
        f"{API}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise RenderError("Render rejected the API key (401).") from exc
        if exc.code == 403:
            raise RenderError(
                "Render returned 403. Either the key lacks permission, or an egress "
                "policy between here and api.render.com is blocking the request."
            ) from exc
        raise RenderError(f"Render returned {exc.code}: {exc.read()[:200]!r}") from exc
    except urllib.error.URLError as exc:
        raise RenderError(
            f"Could not reach api.render.com: {exc.reason}. "
            "If you are behind a filtering proxy, run this from a host that is not."
        ) from exc


#: Exchange credentials. A staging service must not carry any of them.
EXCHANGE_KEYS = ("FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET",
                 "FREQTRADE__EXCHANGE__PASSWORD")


def is_staging(name: str) -> bool:
    return name.lower().endswith("-staging")


def analyse(services: list[dict], env_vars: dict[str, dict[str, str]] | None = None) -> list[dict]:
    """Turn Render's service list into findings. Pure, so it can be tested.

    `env_vars` maps a service name to its variables. When given, the staging
    rules run too: a -staging service must be a dry run, must hold no exchange
    keys, must say ENVIRONMENT=staging, and must not share a database with a
    production service. Values are compared here and never printed.
    """
    env_vars = env_vars or {}
    production_db = {
        (key, value)
        for name, variables in env_vars.items() if not is_staging(name)
        for key, value in variables.items()
        if key in ("SUPABASE_URL", "SUPABASE_DB_URL") and value
    }
    bot_hosts = {}
    for entry in services:
        service = entry.get("service", entry)
        if service.get("type") == "private_service":
            url = ((service.get("serviceDetails") or {}).get("url") or "")
            host = url.split("://")[-1].split(":")[0]
            if host:
                bot_hosts[is_staging(service.get("name", ""))] = host
    findings = []
    for entry in services:
        service = entry.get("service", entry)
        details = service.get("serviceDetails") or {}
        name = service.get("name", "?")
        kind = service.get("type", "?")
        # Render nests region inside serviceDetails, not at the top level.
        # Reading it from the wrong place reports every service as "unknown",
        # which silently turns this audit into a no-op.
        region = (details.get("region") or service.get("region") or "").lower()

        problems: list[str] = []
        notes: list[str] = []

        if region in US_REGIONS:
            problems.append(
                f"region '{region}' is in the US, and KuCoin refuses US IP addresses. "
                "This service cannot trade on KuCoin, and its region cannot be changed "
                "-- it has to be recreated in singapore or frankfurt."
            )
        elif region in GOOD_REGIONS:
            notes.append(f"region '{region}' works with KuCoin")
        elif region:
            notes.append(f"region '{region}' -- verify KuCoin serves it before trusting it")

        # A bot holding API keys should not be publicly reachable. Match the
        # trading process specifically -- "freqtrade-app" also contains
        # "freqtrade" but is meant to be public, so matching that substring
        # would flag the one service that is correct.
        lowered = name.lower()
        is_companion = any(word in lowered for word in
                           ("app", "api", "web", "worker", "dashboard", "ui", "front"))
        looks_like_bot = not is_companion and (
            "bot" in lowered or "trade" in lowered or lowered == "freqtrade"
        )
        if kind == "web_service" and looks_like_bot:
            problems.append(
                "this looks like the trading bot but is a public web service. The "
                "process holding exchange API keys should be a private service (pserv) "
                "so nothing on the internet can reach its order-placing API."
            )

        variables = env_vars.get(name)
        if variables is not None and is_staging(name):
            if looks_like_bot and variables.get("DRY_RUN", "").lower() != "true":
                problems.append("staging bot is not in dry-run: DRY_RUN must be \"true\" on "
                                "every -staging bot, or it can place real orders.")
            held = [k for k in EXCHANGE_KEYS if variables.get(k)]
            if held:
                problems.append(f"staging service holds exchange credentials ({', '.join(held)}). "
                                "Dry-run needs none, and their absence is what makes staging safe.")
            if variables.get("ENVIRONMENT") != "staging":
                problems.append("ENVIRONMENT is not 'staging', so the bot's own guard and the "
                                "dashboard banner cannot tell this apart from production.")
            shared = [k for k in ("SUPABASE_URL", "SUPABASE_DB_URL")
                      if variables.get(k) and (k, variables[k]) in production_db]
            if shared:
                problems.append(f"shares {', '.join(shared)} with a production service: a second "
                                "bot on the production database repoints the live views and can "
                                "evict the production bot through the trading lock.")
        if variables is not None and is_companion and "app" in lowered:
            expected_host = bot_hosts.get(is_staging(name))
            configured = variables.get("FREQTRADE_API_BASE_URL", "")
            if expected_host and configured:
                host = configured.split("://")[-1].split(":")[0]
                port = configured.rsplit(":", 1)[-1] if configured.count(":") >= 2 else ""
                if host != expected_host:
                    problems.append(f"FREQTRADE_API_BASE_URL points at host '{host}', but the "
                                    f"bot's private hostname is '{expected_host}' -- the dashboard "
                                    "will report the bot as unreachable.")
                elif port != "8080":
                    problems.append(f"FREQTRADE_API_BASE_URL uses port '{port}'; the bot listens on "
                                    "8080. The 10000 Render shows in serviceDetails.url refuses "
                                    "connections.")

        findings.append({
            "name": name, "id": service.get("id"), "type": kind,
            "region": region or "unknown",
            "plan": details.get("plan", "?"),
            "suspended": service.get("suspended"),
            "problems": problems, "notes": notes,
        })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--token", default=os.environ.get("RENDER_API_KEY"))
    parser.add_argument("--env", action="store_true",
                        help="also list environment variable NAMES per service")
    args = parser.parse_args()

    if not args.token:
        print("Set RENDER_API_KEY, or pass --token.", file=sys.stderr)
        return 2

    try:
        services = call("/services?limit=100", args.token)
    except RenderError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1

    if not isinstance(services, list):
        print("Unexpected response shape from Render.", file=sys.stderr)
        return 1

    # Values are needed for the staging rules (is DRY_RUN "true"? do two
    # services share a database?) and are compared in memory only.
    env_vars: dict[str, dict[str, str]] = {}
    for entry in services:
        service = entry.get("service", entry)
        if not service.get("id"):
            continue
        try:
            rows = call(f"/services/{service['id']}/env-vars?limit=100", args.token)
        except RenderError as exc:
            print(f"  could not read env vars for {service.get('name')}: {exc}", file=sys.stderr)
            continue
        env_vars[service.get("name", "?")] = {
            (v.get("envVar", v) or {}).get("key", "?"): (v.get("envVar", v) or {}).get("value", "")
            for v in (rows if isinstance(rows, list) else [])
        }

    findings = analyse(services, env_vars)
    print(f"\n{len(findings)} service(s)\n")

    blocking = 0
    for f in findings:
        header = f"  {f['name']}  [{f['type']}, {f['region']}, {f['plan']}]"
        print(header)
        print(f"    id {f['id']}")
        for note in f["notes"]:
            print(f"    ok   {note}")
        for problem in f["problems"]:
            blocking += 1
            for i, line in enumerate(_wrap(problem, 68)):
                print(f"    {'FAIL ' if i == 0 else '     '}{line}")

        if args.env and f["name"] in env_vars:
            # Names only. Values are secrets and several of these are exchange keys.
            names = sorted(env_vars[f["name"]])
            print(f"    env  {', '.join(names) if names else '(none)'}")
        print()

    print(f"  {blocking} blocking problem(s)\n")
    if blocking:
        print("  A region cannot be changed after a service is created. Create the")
        print("  replacements from render.yaml (Render dashboard -> New -> Blueprint),")
        print("  which pins every service to singapore, then delete the old ones once")
        print("  the new deployment is verified.\n")
    return 1 if blocking else 0


def _wrap(text: str, width: int) -> list[str]:
    import textwrap

    return textwrap.wrap(text, width) or [""]


if __name__ == "__main__":
    sys.exit(main())
