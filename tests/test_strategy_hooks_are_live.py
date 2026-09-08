"""A strategy method that freqtrade never calls is worse than one that is absent.

Two of freqtrade's strategy hooks are gated behind an opt-in flag on IStrategy,
both defaulting to False. Writing the method is not enough: without the flag the
method is dead code, freqtrade emits no warning, and the only trace is one INFO
line at startup among two dozen others.

  custom_stoploss        needs  use_custom_stoploss = True
  adjust_trade_position  needs  position_adjustment_enable = True

TrendPullbackStrategy shipped without the first from the day it went live on
2 August, and v3 inherited it. So the ATR-scaled stop, and the tightening to
-0.2% after a partial exit, had never run against real money -- every position
since August was held on the flat -6% instead, while the file said otherwise.

Nothing caught it because nothing was looking. This is what looks.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGIES = sorted((ROOT / "strategies").glob("*.py"))

#: hook method -> the class attribute freqtrade requires to be truthy for it.
GATED_HOOKS = {
    "custom_stoploss": "use_custom_stoploss",
    "adjust_trade_position": "position_adjustment_enable",
}


def declarations(path: Path):
    """The methods and truthy class attributes of the strategy class in a file."""
    tree = ast.parse(path.read_text())
    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)
    if cls is None:
        return set(), set()
    methods = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    enabled = set()
    for node in cls.body:
        if not isinstance(node, ast.Assign):
            continue
        try:
            value = ast.literal_eval(node.value)
        except ValueError:
            continue
        if value:
            enabled |= {t.id for t in node.targets if isinstance(t, ast.Name)}
    return methods, enabled


@pytest.mark.parametrize("path", STRATEGIES, ids=lambda p: p.stem)
def test_no_strategy_defines_a_hook_freqtrade_will_never_call(path):
    methods, enabled = declarations(path)
    dead = [f"{hook}() is defined but {flag} is not set"
            for hook, flag in GATED_HOOKS.items()
            if hook in methods and flag not in enabled]
    assert not dead, (
        f"{path.name}: " + "; ".join(dead)
        + ". freqtrade will not call these, and will not warn you."
    )


def test_the_live_strategy_runs_its_own_stoploss():
    """Named explicitly rather than only covered by the sweep above, because
    this is the one trading real money."""
    methods, enabled = declarations(ROOT / "strategies" / "TrendPullbackStrategy_v3.py")
    assert "custom_stoploss" in methods
    assert "use_custom_stoploss" in enabled
    assert "adjust_trade_position" in methods
    assert "position_adjustment_enable" in enabled


def test_the_gate_list_matches_what_freqtrade_actually_checks():
    """If freqtrade adds or removes a gate, the sweep above silently stops
    covering it. This reads the installed source rather than trusting a list
    written from memory."""
    freqtrade = pytest.importorskip("freqtrade")
    from freqtrade.strategy.interface import IStrategy

    for hook, flag in GATED_HOOKS.items():
        assert hasattr(IStrategy, hook), f"freqtrade no longer has {hook}"
        assert getattr(IStrategy, flag) is False, (
            f"{flag} no longer defaults to False; the gate may have changed"
        )
