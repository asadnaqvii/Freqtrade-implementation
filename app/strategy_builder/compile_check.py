"""Prove a generated strategy actually works before anyone backtests it.

Generating syntactically valid python is not the same as generating a strategy
that runs. This module imports the generated module for real and drives it
against a synthetic dataframe, so a broken indicator reference or a rule that
produces the wrong dtype fails here -- in a request, with a readable message --
rather than thirty seconds into a backtest.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Enough candles that a 200-period indicator still has real values at the end.
_SYNTHETIC_CANDLES = 600


@dataclass
class CompileResult:
    ok: bool
    error: str | None = None
    entry_signals: int | None = None
    exit_signals: int | None = None
    columns: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "error": self.error,
            "entry_signals": self.entry_signals,
            "exit_signals": self.exit_signals,
            "columns": self.columns,
        }


def _synthetic_frame(candles: int = _SYNTHETIC_CANDLES):
    """A price series with enough shape to trigger most rules.

    Deliberately not random: a deterministic frame means a compile check that
    passes today passes tomorrow, and a failure is reproducible.
    """
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2024-01-01", periods=candles, freq="5min", tz="UTC")
    t = np.arange(candles, dtype=float)

    # A drifting sine gives trends, pullbacks and range-bound stretches in one
    # series, so crossovers and oscillator extremes both occur.
    close = 100.0 + t * 0.05 + np.sin(t / 12.0) * 6.0 + np.sin(t / 97.0) * 14.0
    high = close + 0.8
    low = close - 0.8
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = 1000.0 + np.abs(np.sin(t / 7.0)) * 800.0

    return pd.DataFrame(
        {"date": idx, "open": open_, "high": high, "low": low, "close": close, "volume": volume}
    )


def check(source: str, class_name: str) -> CompileResult:
    """Import the generated strategy and run its three populate methods."""
    module_name = f"generated_strategy_{uuid.uuid4().hex}"

    with tempfile.TemporaryDirectory(prefix="strategy-compile-") as tmp:
        path = Path(tmp) / f"{class_name}.py"
        path.write_text(source, encoding="utf-8")

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None or spec.loader is None:  # pragma: no cover
            return CompileResult(ok=False, error="could not build a module spec")

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:
            return CompileResult(ok=False, error=f"import failed: {type(exc).__name__}: {exc}")
        finally:
            sys.modules.pop(module_name, None)

        cls = getattr(module, class_name, None)
        if cls is None:
            return CompileResult(
                ok=False, error=f"module does not define a class named {class_name}"
            )

        try:
            strategy = cls(_minimal_config(cls))
        except Exception as exc:
            return CompileResult(
                ok=False, error=f"could not instantiate: {type(exc).__name__}: {exc}"
            )

        frame = _synthetic_frame()
        meta = {"pair": "BTC/USDT"}

        try:
            frame = strategy.populate_indicators(frame, meta)
        except Exception as exc:
            return CompileResult(
                ok=False, error=f"populate_indicators failed: {type(exc).__name__}: {exc}"
            )

        try:
            frame = strategy.populate_entry_trend(frame, meta)
        except Exception as exc:
            return CompileResult(
                ok=False, error=f"populate_entry_trend failed: {type(exc).__name__}: {exc}"
            )

        try:
            frame = strategy.populate_exit_trend(frame, meta)
        except Exception as exc:
            return CompileResult(
                ok=False, error=f"populate_exit_trend failed: {type(exc).__name__}: {exc}"
            )

        entries = int(frame["enter_long"].fillna(0).sum()) if "enter_long" in frame else 0
        if "enter_short" in frame:
            entries += int(frame["enter_short"].fillna(0).sum())
        exits = int(frame["exit_long"].fillna(0).sum()) if "exit_long" in frame else 0
        if "exit_short" in frame:
            exits += int(frame["exit_short"].fillna(0).sum())

        return CompileResult(
            ok=True,
            entry_signals=entries,
            exit_signals=exits,
            columns=[c for c in frame.columns],
        )


def _minimal_config(cls) -> dict:
    """The smallest config an IStrategy will accept for instantiation."""
    return {
        "stake_currency": "USDT",
        "stake_amount": 100,
        "dry_run": True,
        "trading_mode": "spot",
        "margin_mode": "",
        "runmode": "backtest",
        "timeframe": getattr(cls, "timeframe", "5m"),
        "exchange": {"name": "kucoin"},
    }
