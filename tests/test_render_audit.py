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
