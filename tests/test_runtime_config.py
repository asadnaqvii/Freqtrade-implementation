"""What the bot is actually configured with when it starts.

render_start.py builds the config from environment variables. A
config/config.render.json sat in the repo naming a different strategy
("ActiveTrader"), max_open_trades 2 and a flat stake of 10, none of which was
ever loaded -- editing it changed nothing, silently. It has been deleted rather
than corrected, since a second source of truth would drift again.

These tests pin the settings that came across from config_v3.json -- the
protections especially, which did not exist before and are the only thing that
stops a bad regime compounding.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def build(**env):
    """Compile just the configuration block, with the environment given.

    Importing render_start would configure and launch a bot, so the source
    between the two markers is compiled on its own -- the same approach the
    trading-lock tests take.
    """
    source = (ROOT / "render_start.py").read_text()
    head = source[source.index("def _env(name, default=None):"):source.index("config = {")]
    body = source[source.index("config = {"):]
    body = body[:body.index("\n}\n") + 3]

    previous = {k: os.environ.get(k) for k in env}
    os.environ.update({k: v for k, v in env.items() if v is not None})
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
    try:
        namespace = {"os": os, "secrets": secrets, "json": json,
                     "print": lambda *a, **k: None,
                     "_desired_state": lambda: "running"}
        exec(compile(head, "render_start.py", "exec"), namespace)
        exec(compile(body, "render_start.py", "exec"), namespace)
        return namespace["config"]
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


@pytest.fixture
def config():
    return build()


# ---------------------------------------------------------------------------
# The circuit breakers
# ---------------------------------------------------------------------------

def test_protections_are_never_put_in_the_config(config):
    """This one is written from a live failure, not from the documentation.

    config_v3.json declares protections at config level, so they were ported
    there. freqtrade 2026.7 rejects that outright -- "DEPRECATED: Setting
    'protections' in the configuration is deprecated" is a configuration ERROR,
    not a warning, and the bot refuses to start. Two deploys failed on it.

    The test that shipped alongside that change asserted the protections were
    in the config, which is exactly what broke it. Asserting my own intent
    instead of freqtrade's contract is what let a green suite sit on top of a
    service that could not boot, so this now asserts the contract.
    """
    assert "protections" not in config


def strategy_protections(name):
    """The protections a strategy declares, read from its source.

    Read rather than imported: importing pulls in talib and the whole freqtrade
    strategy stack, which is not what this suite is for.
    """
    import ast
    source = (ROOT / "strategies" / f"{name}.py").read_text()
    tree = ast.parse(source)
    cls = next(n for n in ast.walk(tree)
               if isinstance(n, ast.ClassDef) and n.name == name)
    for node in cls.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", None) == "protections" for t in node.targets):
            return ast.literal_eval(node.value)
    return []


def test_v3_carries_its_own_protections():
    """Where they have to live for freqtrade to accept them."""
    methods = [p["method"] for p in strategy_protections("TrendPullbackStrategy_v3")]
    assert methods == ["CooldownPeriod", "MaxDrawdown", "StoplossGuard"]


def test_a_stopped_out_pair_is_not_re_entered_immediately():
    """The pullback setup can still read as valid on the candle after a
    stop-out, which is how one bad pair takes several bites."""
    cooldown = next(p for p in strategy_protections("TrendPullbackStrategy_v3")
                    if p["method"] == "CooldownPeriod")
    assert cooldown["stop_duration_candles"] >= 1


def test_trading_halts_on_a_drawdown():
    guard = next(p for p in strategy_protections("TrendPullbackStrategy_v3")
                 if p["method"] == "MaxDrawdown")
    assert 0 < guard["max_allowed_drawdown"] <= 0.2, "a 'limit' above 20% limits nothing"
    assert guard["stop_duration_candles"] > 0


def test_repeated_stop_losses_pause_the_bot():
    """Three stops inside a day means the regime is not what the strategy
    assumes. Pausing beats paying to keep finding out."""
    guard = next(p for p in strategy_protections("TrendPullbackStrategy_v3")
                 if p["method"] == "StoplossGuard")
    assert guard["trade_limit"] >= 1
    assert guard["only_per_pair"] is False, "the regime is not one pair's problem"


# ---------------------------------------------------------------------------
# Pair selection
# ---------------------------------------------------------------------------

def test_thin_pairs_are_excluded(config):
    """The floor was 0, so the top 25 by volume could include a pair whose
    spread eats the edge the strategy is trying to capture."""
    assert config["pairlists"][0]["min_value"] >= 1_000_000


def test_the_evidence_based_blacklist_is_applied(config):
    blacklist = config["exchange"]["pair_blacklist"]
    for pair in ("ZEC/USDT", "UNI/USDT", "SHIB/USDT", "PEPE/USDT", "AAVE/USDT"):
        assert pair in blacklist
    # Leveraged tokens are a different kind of exclusion: structural, not
    # evidence-based, and they must survive any edit to the list above.
    assert ".*UP/USDT" in blacklist and ".*DOWN/USDT" in blacklist


def test_the_blacklist_can_be_changed_without_a_deploy(config):
    assert build(FREQTRADE_PAIR_BLACKLIST="FOO/USDT, BAR/USDT")[
        "exchange"]["pair_blacklist"] == ["FOO/USDT", "BAR/USDT"]


def test_an_empty_blacklist_is_honoured_not_replaced_by_the_default():
    """Clearing the list has to be expressible. Quietly reinstating one somebody
    meant to remove is a surprise that only shows up in a trade."""
    assert build(FREQTRADE_PAIR_BLACKLIST="")["exchange"]["pair_blacklist"] == []


# ---------------------------------------------------------------------------
# Things the platform itself depends on
# ---------------------------------------------------------------------------

def test_the_local_api_is_always_enabled(config):
    """config_v3.json ships with api_server disabled. Every control this
    platform has goes through that API: the Start and Stop buttons, the state
    the heartbeat reports, signal capture, and the lock handover on deploy. A
    bot with it off is one nothing can stop."""
    assert config["api_server"]["enabled"] is True
    assert config["api_server"]["listen_port"] > 0


def test_the_api_password_is_never_a_shipped_constant():
    """A generated secret beats a weak default that survives into production."""
    generated = build(JWT_SECRET_KEY=None)["api_server"]["jwt_secret_key"]
    assert len(generated) >= 32


def test_headroom_is_left_for_fees_and_slippage(config):
    assert 0.5 <= config["tradable_balance_ratio"] <= 0.97


def test_the_strategy_is_switchable_by_environment():
    """The whole point: swapping strategies, and rolling the swap back, is an
    environment change rather than a code deploy."""
    source = (ROOT / "render_start.py").read_text()
    assert 'strategy = _env("FREQTRADE_STRATEGY"' in source


def test_the_v3_strategy_is_present_to_switch_to():
    path = ROOT / "strategies" / "TrendPullbackStrategy_v3.py"
    assert path.exists()
    assert "class TrendPullbackStrategy_v3" in path.read_text()


# ---------------------------------------------------------------------------
# The check that would have caught the failed deploys
# ---------------------------------------------------------------------------

def test_freqtrade_itself_accepts_the_config(config):
    """Validate against freqtrade's own rules, not against my reading of them.

    Two deploys failed on a key freqtrade rejects, while this suite stayed
    green, because every other test here asserted what I meant the config to
    say. Only freqtrade can answer whether freqtrade will start.

    Skipped when freqtrade is not installed so the suite still runs anywhere;
    it is installed wherever the bot is built, which is where it matters.
    """
    pytest.importorskip("freqtrade")
    from freqtrade.configuration.config_validation import validate_config_schema
    from freqtrade.configuration.deprecated_settings import (
        process_temporary_deprecated_settings,
    )

    process_temporary_deprecated_settings(config)
    validate_config_schema(config)


def test_the_guard_above_actually_catches_the_failure_it_was_written_for():
    """A guard nobody has seen fail is a guard nobody knows works.

    The first version of the test above looped over functions matching a name
    I guessed at, matched nothing, and passed on the exact config that had just
    broken production twice. This feeds it that config and requires a refusal.
    """
    pytest.importorskip("freqtrade")
    from freqtrade.configuration.deprecated_settings import (
        process_temporary_deprecated_settings,
    )

    with pytest.raises(Exception) as failure:
        process_temporary_deprecated_settings(
            {"protections": [{"method": "CooldownPeriod", "stop_duration_candles": 3}]})
    assert "protections" in str(failure.value).lower()
