#!/usr/bin/env bash
set -euo pipefail

# One build for all three services. They share a dependency set; which one you
# get is decided by the start command, not the build.
#
# The hand-rolled TA-Lib C compilation that used to live here is gone: TA-Lib
# publishes prebuilt wheels now, so `pip install TA-Lib` just works and the
# build no longer spends several minutes running ./configure && make.

echo "=== build ==="
python --version

pip install --upgrade pip
pip install --no-cache-dir -r requirements-render.txt

echo "=== verifying imports ==="
python - <<'PY'
import importlib, sys

required = ["freqtrade", "talib", "ccxt", "fastapi", "uvicorn", "httpx", "jwt", "pydantic",
            "pandas", "numpy", "scipy", "psycopg2"]
missing = []
for name in required:
    try:
        module = importlib.import_module(name)
        print(f"  ok   {name} {getattr(module, '__version__', '')}")
    except Exception as exc:
        missing.append(f"{name}: {exc}")
        print(f"  FAIL {name}: {exc}")

if missing:
    # Fail the build rather than discovering this at 3am when the bot restarts.
    sys.exit("missing dependencies: " + "; ".join(missing))
PY

mkdir -p user_data/strategies user_data/data user_data/logs user_data/backtest_results
cp strategies/*.py user_data/strategies/ 2>/dev/null || true

echo "=== build complete ==="
