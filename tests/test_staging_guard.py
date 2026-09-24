"""A bot outside production can never trade live.

Two settings decide whether a deployment can reach the real account,
ENVIRONMENT and DRY_RUN, and the guard in render_start.py refuses to start when
they disagree. render_start.py cannot be imported (it configures and launches a
bot), so the guard functions are compiled out of the source on their own, the
way test_trading_lock.py does it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "render_start.py").read_text()


def load_guard():
    start = SOURCE.index("def _refuse_live_outside_production(")
    end = SOURCE.index("_refusal = _refuse_live_outside_production(")
    namespace: dict = {}
    exec(compile(SOURCE[start:end], "render_start.py", "exec"), namespace)
    return namespace


def test_a_non_production_environment_refuses_to_trade_live():
    guard = load_guard()["_refuse_live_outside_production"]
    message = guard("staging", False)
    assert message and message.startswith("REFUSING TO START")
    assert "DRY_RUN" in message


def test_production_is_allowed_to_trade_live():
    guard = load_guard()["_refuse_live_outside_production"]
    assert guard("production", False) is None


def test_a_dry_run_is_allowed_anywhere():
    guard = load_guard()["_refuse_live_outside_production"]
    assert guard("staging", True) is None
    assert guard("production", True) is None
    assert guard("anything-else", True) is None


def test_the_refusal_fails_the_process_before_a_config_is_written():
    # The guard has to run before the freqtrade config is assembled: a refusal
    # that came after writing config.json would still leave a live config on disk.
    refusal_at = SOURCE.index("_refusal = _refuse_live_outside_production(")
    exit_at = SOURCE.index("sys.exit(1)", refusal_at)
    config_at = SOURCE.index("config = {")
    assert refusal_at < exit_at < config_at


def test_credentials_carried_by_a_dry_run_are_named():
    warn = load_guard()["_credentials_a_dry_run_does_not_need"]
    message = warn(True, ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET"])
    assert message and "FREQTRADE__EXCHANGE__KEY" in message
    assert "FREQTRADE__EXCHANGE__SECRET" in message


def test_a_dry_run_without_credentials_is_quiet():
    warn = load_guard()["_credentials_a_dry_run_does_not_need"]
    assert warn(True, []) is None
    assert warn(False, ["FREQTRADE__EXCHANGE__KEY"]) is None


def test_the_config_endpoint_names_the_environment(monkeypatch):
    # The browser reads /api/config before anyone signs in, so this is what
    # lets the page say STAGING before a password is typed into it.
    from app.core.config import get_settings
    from app.api.routers import health

    monkeypatch.setenv("ENVIRONMENT", "staging")
    get_settings.cache_clear()
    try:
        assert asyncio.run(health.public_config())["environment"] == "staging"
        assert asyncio.run(health.health())["environment"] == "staging"
    finally:
        get_settings.cache_clear()
