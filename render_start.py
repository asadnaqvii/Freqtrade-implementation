#!/usr/bin/env python3
import os
import sys
import json

print("=== Render Start Script ===")
print(f"Python: {sys.version}")
print(f"HOME: {os.path.expanduser('~')}")
print(f"PWD: {os.getcwd()}")

# Set TA-Lib library path for runtime - check multiple possible locations
ta_lib_candidates = [
    os.path.join(os.path.expanduser("~"), "ta-lib", "lib"),
    "/opt/render/project/ta-lib/lib",
    "/opt/render/.local/ta-lib/lib",
]
current_ld = os.environ.get("LD_LIBRARY_PATH", "")
for ta_lib_path in ta_lib_candidates:
    if os.path.exists(ta_lib_path) and ta_lib_path not in current_ld:
        current_ld = f"{ta_lib_path}:{current_ld}" if current_ld else ta_lib_path
        print(f"Found TA-Lib at: {ta_lib_path}")
os.environ["LD_LIBRARY_PATH"] = current_ld
print(f"LD_LIBRARY_PATH: {current_ld}")

# Verify TA-Lib can be loaded
try:
    import talib
    print(f"TA-Lib Python module loaded successfully (version: {talib.__version__})")
except ImportError as e:
    print(f"WARNING: TA-Lib import failed: {e}")
    print("Attempting to find libta_lib.so...")
    for path in ta_lib_candidates:
        so_file = os.path.join(path, "libta_lib.so.0")
        if os.path.exists(so_file):
            print(f"  Found: {so_file}")
        else:
            print(f"  Not found: {so_file}")

# Validate required environment variables
required_vars = ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET", "FREQTRADE__EXCHANGE__PASSWORD"]
missing = [v for v in required_vars if not os.environ.get(v)]
if missing:
    print(f"WARNING: Missing environment variables: {', '.join(missing)}")
    print("Bot will start but cannot connect to exchange for live trading.")

port = int(os.environ.get("PORT", 10000))

# Create config from environment variables
config = {
    "max_open_trades": 6,
    "stake_currency": "USDT",
    "stake_amount": 10,
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": os.environ.get("DRY_RUN", "false").lower() == "true",
    "dry_run_wallet": 1000,
    "cancel_open_orders_on_exit": False,
    "unfilledtimeout": {
        "entry": 10,
        "exit": 10,
        "exit_timeout_count": 0,
        "unit": "minutes"
    },
    "entry_pricing": {
        "price_side": "same",
        "use_order_book": True,
        "order_book_top": 1,
        "price_last_balance": 0.0,
        "check_depth_of_market": {
            "enabled": False,
            "bids_to_ask_delta": 1
        }
    },
    "exit_pricing": {
        "price_side": "same",
        "use_order_book": True,
        "order_book_top": 1
    },
    "exchange": {
        "name": "kucoin",
        "key": os.environ.get("FREQTRADE__EXCHANGE__KEY", ""),
        "secret": os.environ.get("FREQTRADE__EXCHANGE__SECRET", ""),
        "password": os.environ.get("FREQTRADE__EXCHANGE__PASSWORD", ""),
        "ccxt_config": {},
        "ccxt_async_config": {
            "aiohttp_trust_env": True
        },
        "pair_whitelist": [
            "BTC/USDT",
            "ETH/USDT",
            "SOL/USDT",
            "ADA/USDT"
        ],
        "pair_blacklist": []
    },
    "pairlists": [
        {
            "method": "StaticPairList"
        }
    ],
    "edge": {
        "enabled": False
    },
    "api_server": {
        "enabled": True,
        "listen_ip_address": "0.0.0.0",
        "listen_port": port,
        "verbosity": "error",
        "enable_openapi": True,
        "jwt_secret_key": os.environ.get("JWT_SECRET_KEY", "supersecretkey"),
        "CORS_origins": ["*"],
        "username": os.environ.get("API_USERNAME", "freqtrader"),
        "password": os.environ.get("API_PASSWORD", "freqtrader")
    },
    "bot_name": "freqtrade",
    "initial_state": "running",
    "force_entry_enable": True,
    "internals": {
        "process_throttle_secs": 5
    },
    "strategy": "TrendPullbackStrategy",
    # Hard backstop matching the strategy's design. The real exit logic is the
    # strategy's ATR-based custom_stoploss; a config stoploss of -0.005 (0.5%)
    # would override that and stop every trade out almost instantly.
    "stoploss": -0.06
}

# Write config file
os.makedirs("config", exist_ok=True)
with open("config/config.json", "w") as f:
    json.dump(config, f, indent=2)

print(f"Config created: dry_run={config['dry_run']}, port={port}")
print(f"Exchange key configured: {'Yes' if config['exchange']['key'] else 'No'}")

# Copy strategies to user_data
os.makedirs("user_data/strategies", exist_ok=True)
os.system("cp strategies/*.py user_data/strategies/ 2>/dev/null || true")

# Start freqtrade with stdout/stderr unbuffered
print(f"Starting freqtrade on port {port}...", flush=True)
sys.stdout.flush()
sys.stderr.flush()

os.execvp("freqtrade", [
    "freqtrade", "trade",
    "--config", "config/config.json",
    "--strategy", "TrendPullbackStrategy",
    "--strategy-path", "strategies",
    "--userdir", "user_data"
])
