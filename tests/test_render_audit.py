"""Tests for the Render audit logic.

The network call cannot be exercised here, but `analyse` is pure, so the part
that actually makes a judgement is tested against Render's response shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from render_audit import analyse  # noqa: E402


def service(*, region="oregon", plan="starter", **kw):
    # Render puts region and plan inside serviceDetails, which is exactly where
    # an earlier version of this code failed to look.
    base = {"id": "srv-1", "name": "svc", "type": "web_service",
            "serviceDetails": {"plan": plan, "region": region}}
    base.update(kw)
    return {"service": base}


def test_us_region_is_flagged_as_blocking():
    [found] = analyse([service(region="oregon", name="freqtrade-bot")])
    assert any("US" in p for p in found["problems"])


def test_each_us_region_is_caught():
    for region in ("oregon", "ohio", "virginia"):
        [found] = analyse([service(region=region, name="bot")])
        assert found["problems"], f"{region} should be flagged"


def test_singapore_passes():
    [found] = analyse([service(region="singapore", name="freqtrade-bot", type="private_service")])
    assert found["problems"] == []
    assert any("works with KuCoin" in n for n in found["notes"])


def test_public_bot_is_flagged_even_in_a_good_region():
    # A bot holding exchange keys should not be internet-reachable.
    [found] = analyse([service(region="singapore", name="freqtrade-bot", type="web_service")])
    assert any("private service" in p for p in found["problems"])


def test_a_public_app_service_is_not_flagged():
    [found] = analyse([service(region="singapore", name="freqtrade-app", type="web_service")])
    assert found["problems"] == []


def test_unknown_region_is_a_note_not_a_failure():
    [found] = analyse([service(region="mars", name="app", type="web_service")])
    assert found["problems"] == []
    assert any("verify" in n for n in found["notes"])


def test_handles_a_flat_response_shape():
    # Render has returned both {"service": {...}} and the bare object.
    [found] = analyse([{"id": "srv-2", "name": "flat", "type": "worker", "region": "singapore"}])
    assert found["name"] == "flat"
    assert found["region"] == "singapore"


def test_region_is_read_from_service_details():
    [found] = analyse([service(region="oregon", name="bot")])
    assert found["region"] == "oregon"
    assert found["problems"], "a region read as 'unknown' would silently pass"


def test_plan_is_read_from_service_details():
    [found] = analyse([service(region="singapore", name="app", plan="standard")])
    assert found["plan"] == "standard"


# ── staging rules ──────────────────────────────────────────────────────────

def staging_set(*, dry_run="true", keys=False, environment="staging",
                supabase_url="https://staging.supabase.co", api_base="http://freqtrade-bot-staging-ab12:8080"):
    services = [
        service(region="singapore", name="freqtrade-bot", type="private_service",
                serviceDetails={"plan": "standard", "region": "singapore", "url": "freqtrade-bot-hn7v:10000"}),
        service(region="singapore", name="freqtrade-app", type="web_service"),
        service(region="singapore", name="freqtrade-bot-staging", type="private_service",
                serviceDetails={"plan": "standard", "region": "singapore", "url": "freqtrade-bot-staging-ab12:10000"}),
        service(region="singapore", name="freqtrade-app-staging", type="web_service"),
    ]
    bot = {"DRY_RUN": dry_run, "ENVIRONMENT": environment, "SUPABASE_URL": supabase_url,
           "SUPABASE_DB_URL": "postgresql://x@staging:5432/postgres"}
    if keys:
        bot["FREQTRADE__EXCHANGE__KEY"] = "k"
    env = {
        "freqtrade-bot": {"DRY_RUN": "false", "ENVIRONMENT": "production",
                          "SUPABASE_URL": "https://prod.supabase.co",
                          "SUPABASE_DB_URL": "postgresql://x@prod:5432/postgres"},
        "freqtrade-app": {"SUPABASE_URL": "https://prod.supabase.co",
                          "FREQTRADE_API_BASE_URL": "http://freqtrade-bot-hn7v:8080"},
        "freqtrade-bot-staging": bot,
        "freqtrade-app-staging": {"SUPABASE_URL": supabase_url, "ENVIRONMENT": environment,
                                  "FREQTRADE_API_BASE_URL": api_base},
    }
    return {f["name"]: f for f in analyse(services, env)}


def test_a_correctly_configured_staging_set_passes():
    found = staging_set()
    assert all(f["problems"] == [] for f in found.values()), found


def test_a_staging_bot_that_is_not_dry_run_is_flagged():
    found = staging_set(dry_run="false")
    assert any("dry-run" in p for p in found["freqtrade-bot-staging"]["problems"])


def test_a_staging_bot_holding_exchange_keys_is_flagged():
    found = staging_set(keys=True)
    assert any("exchange credentials" in p for p in found["freqtrade-bot-staging"]["problems"])


def test_a_staging_service_sharing_the_production_database_is_flagged():
    found = staging_set(supabase_url="https://prod.supabase.co")
    assert any("production" in p for p in found["freqtrade-bot-staging"]["problems"])
    assert any("production" in p for p in found["freqtrade-app-staging"]["problems"])


def test_a_staging_service_not_marked_as_staging_is_flagged():
    found = staging_set(environment="production")
    assert any("ENVIRONMENT" in p for p in found["freqtrade-bot-staging"]["problems"])


def test_the_app_must_point_at_its_own_bots_private_hostname():
    found = staging_set(api_base="http://freqtrade-bot-hn7v:8080")
    assert any("private hostname" in p for p in found["freqtrade-app-staging"]["problems"])


def test_the_app_must_use_the_port_the_bot_listens_on():
    found = staging_set(api_base="http://freqtrade-bot-staging-ab12:10000")
    assert any("8080" in p for p in found["freqtrade-app-staging"]["problems"])


def test_production_services_are_not_held_to_the_staging_rules():
    found = staging_set()
    assert found["freqtrade-bot"]["problems"] == []
