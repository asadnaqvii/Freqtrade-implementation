"""Which exact strategy, parameters and configuration produced a decision.

A decision that cannot be traced to the code that made it cannot be learned
from. This hashes the three things that can change between two decisions
that look the same: the strategy file, the values its parameters were
running at, and the runtime config (with every secret removed first).
Usable from the live bot and from a backtest, which is the phase-9 seam.
"""

from __future__ import annotations

import hashlib
import os
from typing import Any

from app.learning.canonical import canonical_json

#: Bumped by hand whenever the set of indicator columns the strategy exposes
#: changes. Two snapshots with different versions are not comparable.
FEATURE_SET_VERSION = "tp-v3.1"

#: The adapter's own version, recorded on every decision it captures.
ADAPTER_VERSION = "1.0"

_SECRET_PATHS = (
    ("exchange", "key"), ("exchange", "secret"), ("exchange", "password"),
    ("exchange", "uid"), ("api_server", "jwt_secret_key"), ("api_server", "password"),
    ("api_server", "username"), ("telegram", "token"), ("telegram", "chat_id"),
    ("discord", "webhook_url"), ("webhook", "url"),
)


def strategy_sha(strategy_name: str, search_dirs: tuple[str, ...] = ("strategies", "user_data/strategies")) -> str | None:
    """sha256 of the strategy's source file, or None when it cannot be found."""
    for directory in search_dirs:
        path = os.path.join(directory, f"{strategy_name}.py")
        if os.path.exists(path):
            with open(path, "rb") as handle:
                return hashlib.sha256(handle.read()).hexdigest()
    return None


def parameter_values(strategy: Any) -> dict[str, Any]:
    """The values every hyperopt parameter is running at. {} when none."""
    values: dict[str, Any] = {}
    enumerate_parameters = getattr(strategy, "enumerate_parameters", None)
    if enumerate_parameters is None:
        return values
    try:
        for name, parameter in enumerate_parameters():
            values[str(name)] = getattr(parameter, "value", None)
    except Exception:  # noqa: BLE001 - provenance must never stop a decision
        return {}
    return values


def parameter_hash(strategy: Any) -> str | None:
    values = parameter_values(strategy)
    if not values:
        return None
    return hashlib.sha256(canonical_json(values).encode("utf-8")).hexdigest()


def redacted_config(config: dict) -> dict:
    """The runtime config with every credential removed."""
    scrubbed: dict = {}
    for key, value in config.items():
        scrubbed[key] = dict(value) if isinstance(value, dict) else value
    for section, key in _SECRET_PATHS:
        if isinstance(scrubbed.get(section), dict) and key in scrubbed[section]:
            scrubbed[section][key] = "<redacted>"
    return scrubbed


def config_hash(config: dict | None) -> str | None:
    if not config:
        return None
    return hashlib.sha256(canonical_json(redacted_config(config)).encode("utf-8")).hexdigest()


def build_provenance(*, strategy_name: str, strategy: Any = None, config: dict | None = None,
                     freqtrade_version: str | None = None, run_id: str | None = None,
                     environment: str | None = None, extra: dict | None = None) -> dict:
    """Everything needed to say "this exact code, these exact settings"."""
    provenance = {
        "strategy_id": strategy_name,
        "strategy_code_hash": strategy_sha(strategy_name),
        "strategy_version": str(getattr(strategy, "INTERFACE_VERSION", "")) or None,
        "parameter_hash": parameter_hash(strategy) if strategy is not None else None,
        "runtime_config_hash": config_hash(config),
        "feature_set_version": FEATURE_SET_VERSION,
        "adapter_version": ADAPTER_VERSION,
        "freqtrade_version": freqtrade_version,
        "run_id": run_id,
        "environment": environment,
    }
    if extra:
        provenance.update(extra)
    return provenance
