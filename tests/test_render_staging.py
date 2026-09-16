"""The staging blueprint reaches Render through scripts/render_staging.py.

The API calls cannot run here; the parts that decide what to send are pure and
are tested against the real render.staging.yaml, so a change to the yaml that
would create something other than staging fails before it is ever applied.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import render_staging as rs  # noqa: E402


def specs():
    return {s["name"]: s for s in rs.load_blueprint()}


def test_every_staging_service_is_named_branched_and_marked_as_staging():
    for spec in rs.load_blueprint():
        env, _ = rs.desired_env(spec, environ={})
        assert rs.check_is_staging(spec, env) == []


def test_the_staging_bot_is_dry_run_and_holds_no_exchange_keys():
    env, _ = rs.desired_env(specs()["freqtrade-bot-staging"], environ={})
    assert env["DRY_RUN"] == "true"
    assert env["ENVIRONMENT"] == "staging"
    assert env["BOT_NAME"] == "freqtrade-bot-staging"
    for key in rs.FORBIDDEN:
        assert key not in env


def test_a_service_that_would_trade_live_is_refused():
    spec = dict(specs()["freqtrade-bot-staging"])
    spec["envVars"] = [{"key": "DRY_RUN", "value": "false"}, {"key": "ENVIRONMENT", "value": "staging"}]
    env, _ = rs.desired_env(spec, environ={})
    assert any("DRY_RUN" in p for p in rs.check_is_staging(spec, env))


def test_exchange_keys_are_refused_even_if_someone_adds_them_to_the_yaml():
    spec = dict(specs()["freqtrade-bot-staging"])
    spec["envVars"] = spec["envVars"] + [{"key": "FREQTRADE__EXCHANGE__KEY", "value": "x"}]
    env, _ = rs.desired_env(spec, environ={})
    assert any("FREQTRADE__EXCHANGE__KEY" in p for p in rs.check_is_staging(spec, env))


def test_secrets_come_from_the_environment_and_are_named_when_missing():
    spec = specs()["freqtrade-worker-staging"]
    env, missing = rs.desired_env(spec, environ={"STAGING_SUPABASE_URL": "https://x.supabase.co"})
    assert env["SUPABASE_URL"] == "https://x.supabase.co"
    assert missing == ["SUPABASE_SERVICE_ROLE_KEY"]


def test_an_existing_secret_on_render_is_kept_when_the_environment_lacks_it():
    spec = specs()["freqtrade-worker-staging"]
    env, missing = rs.desired_env(spec, environ={}, existing={"SUPABASE_SERVICE_ROLE_KEY": "kept"})
    assert env["SUPABASE_SERVICE_ROLE_KEY"] == "kept"
    assert "SUPABASE_SERVICE_ROLE_KEY" not in missing


def test_a_generated_secret_is_minted_once_and_then_kept():
    spec = specs()["freqtrade-bot-staging"]
    first, _ = rs.desired_env(spec, environ={})
    again, _ = rs.desired_env(spec, environ={}, existing={"JWT_SECRET_KEY": first["JWT_SECRET_KEY"]})
    assert len(first["JWT_SECRET_KEY"]) >= 40
    assert again["JWT_SECRET_KEY"] == first["JWT_SECRET_KEY"]


def test_the_create_payload_carries_the_things_render_gets_wrong_by_default():
    spec = specs()["freqtrade-worker-staging"]
    env, _ = rs.desired_env(spec, environ={})
    payload = rs.create_payload(spec, env, "tea-1", "https://github.com/o/r")
    assert payload["type"] == "background_worker"
    assert payload["branch"] == "staging"
    assert payload["serviceDetails"]["region"] == "singapore"
    assert payload["serviceDetails"]["disk"] == {"name": "candles-staging", "mountPath": "/data", "sizeGB": 10}
    assert {"key": "ENVIRONMENT", "value": "staging"} in payload["envVars"]


def test_the_private_address_uses_the_port_the_bot_listens_on():
    # Render reports :10000 in serviceDetails.url; the bot listens on PORT, 8080.
    service = {"type": "private_service", "serviceDetails": {"url": "freqtrade-bot-staging-ab12:10000"}}
    assert rs.private_url(service) == "http://freqtrade-bot-staging-ab12:8080"
    assert rs.private_url({"type": "web_service", "serviceDetails": {"url": "https://x"}}) is None
