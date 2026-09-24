#!/usr/bin/env python3
"""Create or update the staging services on Render from render.staging.yaml.

Render's blueprint sync is a dashboard-only feature. This is the same idea
driven through the API, so the yaml stays the one description of staging and
this script is the only way it reaches Render.

    export RENDER_API_KEY=rnd_...
    # one STAGING_<KEY> per `sync: false` entry in the yaml, e.g.
    export STAGING_SUPABASE_URL=https://....supabase.co
    python scripts/render_staging.py --apply     # create what is missing, sync env vars
    python scripts/render_staging.py --show      # ids, private hostnames, latest deploy
    python scripts/render_staging.py --deploy    # start a deploy of each service

Secrets are read from STAGING_<KEY> and never printed. One that is not set is
left as it is on Render (or absent on a new service) and named in the output,
so nothing ships without a secret silently.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import urllib.error
import urllib.request
from pathlib import Path

import yaml

API = "https://api.render.com/v1"
DEFAULT_REPO = "https://github.com/asadnaqvii/Freqtrade-implementation"
BLUEPRINT = Path(__file__).resolve().parents[1] / "render.staging.yaml"

#: Blueprint service types -> the API's names for them.
TYPES = {"pserv": "private_service", "web": "web_service", "worker": "background_worker"}

#: Every service the yaml describes must carry one of these, or it is not
#: staging and this script refuses to touch it. The guard is here as well as in
#: render_start.py because the cheapest place to stop a mistake is before it
#: is deployed.
STAGING_MARKS = {"ENVIRONMENT": "staging", "DRY_RUN": "true"}
FORBIDDEN = ("FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET",
             "FREQTRADE__EXCHANGE__PASSWORD", "HEARTBEAT_URL", "ALERT_WEBHOOK_URL")


class RenderError(RuntimeError):
    pass


def call(method: str, path: str, token: str, body: object | None = None) -> object:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(
        f"{API}{path}", data=data, method=method,
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                 "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read().decode()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raise RenderError(f"{method} {path} -> {exc.code}: {exc.read()[:400]!r}") from exc


def load_blueprint(path: Path = BLUEPRINT) -> list[dict]:
    return yaml.safe_load(path.read_text())["services"]


def desired_env(spec: dict, environ: dict | None = None,
                existing: dict | None = None) -> tuple[dict, list[str]]:
    """The env vars a service should end up with, and the secrets we lack.

    Literal values come from the yaml. `sync: false` values come from
    STAGING_<KEY> in the environment, falling back to whatever the service
    already has. `generateValue` keys are minted here once and then kept.
    """
    environ = os.environ if environ is None else environ
    existing = existing or {}
    out: dict = {}
    missing: list[str] = []
    for item in spec.get("envVars", []):
        key = item["key"]
        if "value" in item:
            out[key] = str(item["value"])
        elif item.get("generateValue"):
            out[key] = existing.get(key) or secrets.token_urlsafe(48)
        elif item.get("sync") is False:
            value = environ.get(f"STAGING_{key}") or existing.get(key)
            if value:
                out[key] = value
            else:
                missing.append(key)
    return out, missing


def check_is_staging(spec: dict, env: dict) -> list[str]:
    """Reasons this service must not be applied as staging; empty when fine."""
    problems = []
    if not spec["name"].endswith("-staging"):
        problems.append(f"{spec['name']}: name does not end in -staging")
    if spec.get("branch") != "staging":
        problems.append(f"{spec['name']}: branch is {spec.get('branch')!r}, not 'staging'")
    if spec["type"] == "pserv":
        for key, want in STAGING_MARKS.items():
            if env.get(key) != want:
                problems.append(f"{spec['name']}: {key} must be {want!r}, is {env.get(key)!r}")
    for key in FORBIDDEN:
        if key in env:
            problems.append(f"{spec['name']}: {key} must not be set on staging")
    return problems


def create_payload(spec: dict, env: dict, owner_id: str, repo: str) -> dict:
    details: dict = {
        "runtime": spec.get("runtime", "python"),
        "plan": spec["plan"],
        "region": spec["region"],
        "envSpecificDetails": {
            "buildCommand": spec["buildCommand"],
            "startCommand": spec["startCommand"],
        },
        "numInstances": 1,
        "pullRequestPreviewsEnabled": "no",
    }
    if spec.get("healthCheckPath"):
        details["healthCheckPath"] = spec["healthCheckPath"]
    if spec.get("disk"):
        details["disk"] = {
            "name": spec["disk"]["name"],
            "mountPath": spec["disk"]["mountPath"],
            "sizeGB": int(spec["disk"]["sizeGB"]),
        }
    return {
        "type": TYPES[spec["type"]],
        "name": spec["name"],
        "ownerId": owner_id,
        "repo": repo,
        "branch": spec.get("branch", "staging"),
        "autoDeploy": "yes",
        "rootDir": "",
        "envVars": [{"key": k, "value": v} for k, v in sorted(env.items())],
        "serviceDetails": details,
    }


def private_url(service: dict) -> str | None:
    """The address other services reach a private service at.

    Render reports `<name>-<suffix>:10000` and the 10000 is wrong: the service
    listens on PORT (8080). Hand back the host with the right port, or None for
    anything that is not a private service.
    """
    if service.get("type") != "private_service":
        return None
    url = (service.get("serviceDetails") or {}).get("url") or ""
    host = url.split("://")[-1].split(":")[0]
    return f"http://{host}:8080" if host else None


def find_service(name: str, token: str) -> dict | None:
    for entry in call("GET", f"/services?name={name}&limit=20", token) or []:
        service = entry.get("service", entry)
        if service.get("name") == name:
            return service
    return None


def current_env(service_id: str, token: str) -> dict:
    rows = call("GET", f"/services/{service_id}/env-vars?limit=100", token) or []
    return {(r.get("envVar", r))["key"]: (r.get("envVar", r)).get("value", "") for r in rows}


def apply(specs: list[dict], token: str, owner_id: str, repo: str) -> int:
    failures = 0
    for spec in specs:
        service = find_service(spec["name"], token)
        existing = current_env(service["id"], token) if service else {}
        env, missing = desired_env(spec, existing=existing)
        problems = check_is_staging(spec, env)
        if problems:
            failures += 1
            for p in problems:
                print(f"  REFUSED  {p}")
            continue
        if service:
            call("PUT", f"/services/{service['id']}/env-vars", token,
                 [{"key": k, "value": v} for k, v in sorted(env.items())])
            print(f"  updated  {spec['name']}  {service['id']}  env vars synced")
        else:
            created = call("POST", "/services", token, create_payload(spec, env, owner_id, repo))
            service = created.get("service", created)
            print(f"  created  {spec['name']}  {service['id']}")
        for key in missing:
            print(f"           missing secret STAGING_{key} -- not set on Render")
        if url := private_url(service):
            print(f"           private address {url}  (this is FREQTRADE_API_BASE_URL)")
    return failures


def show(specs: list[dict], token: str) -> None:
    for spec in specs:
        service = find_service(spec["name"], token)
        if not service:
            print(f"  {spec['name']}: not created")
            continue
        details = service.get("serviceDetails") or {}
        deploys = call("GET", f"/services/{service['id']}/deploys?limit=1", token) or []
        latest = (deploys[0].get("deploy", deploys[0]) if deploys else {}) or {}
        print(f"  {spec['name']}  {service['id']}  {details.get('plan')}/{details.get('region')}"
              f"  branch={service.get('branch')}  deploy={latest.get('status', '-')}")
        if url := private_url(service):
            print(f"      private address {url}")
        if service.get("type") == "web_service":
            print(f"      public  {details.get('url')}")


def deploy(specs: list[dict], token: str) -> None:
    for spec in specs:
        service = find_service(spec["name"], token)
        if not service:
            print(f"  {spec['name']}: not created, nothing to deploy")
            continue
        result = call("POST", f"/services/{service['id']}/deploys", token,
                      {"clearCache": "do_not_clear"})
        print(f"  {spec['name']}: deploy {(result or {}).get('id', '?')} started")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--token", default=os.environ.get("RENDER_API_KEY"))
    parser.add_argument("--repo", default=os.environ.get("RENDER_REPO", DEFAULT_REPO))
    parser.add_argument("--blueprint", type=Path, default=BLUEPRINT)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--deploy", action="store_true")
    parser.add_argument("--only", default="",
                        help="comma-separated service names to act on (default: all)")
    args = parser.parse_args()
    if not args.token:
        print("Set RENDER_API_KEY, or pass --token.", file=sys.stderr)
        return 2
    if not (args.apply or args.show or args.deploy):
        parser.print_help()
        return 2

    specs = load_blueprint(args.blueprint)
    if args.only:
        wanted = {name.strip() for name in args.only.split(",") if name.strip()}
        unknown = wanted - {spec["name"] for spec in specs}
        if unknown:
            print(f"not in the blueprint: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        specs = [spec for spec in specs if spec["name"] in wanted]
    try:
        if args.apply:
            owners = call("GET", "/owners?limit=10", args.token) or []
            owner_id = (owners[0].get("owner", owners[0]))["id"]
            print("\napplying render.staging.yaml\n")
            if apply(specs, args.token, owner_id, args.repo):
                return 1
        if args.show:
            print("\nstaging services\n")
            show(specs, args.token)
        if args.deploy:
            print("\ndeploying\n")
            deploy(specs, args.token)
    except RenderError as exc:
        print(f"\nERROR: {exc}\n", file=sys.stderr)
        return 1
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
