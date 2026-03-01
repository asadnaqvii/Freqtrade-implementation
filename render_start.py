#!/usr/bin/env python3
import os
import sys
import json
import subprocess

# Set TA-Lib library path for runtime
ta_lib_path = os.path.join(os.path.expanduser("~"), "ta-lib", "lib")
if os.path.exists(ta_lib_path):
    current_ld = os.environ.get("LD_LIBRARY_PATH", "")
    os.environ["LD_LIBRARY_PATH"] = f"{ta_lib_path}:{current_ld}" if current_ld else ta_lib_path

# Validate required environment variables
required_vars = ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET", "FREQTRADE__EXCHANGE__PASSWORD"]
missing = [v for v in required_vars if not os.environ.get(v)]
if missing:
    print(f"WARNING: Missing environment variables: {', '.join(missing)}")
    print("Bot will start but cannot connect to exchange for live trading.")

# Create config from environment variables
config = {
    "max_open_trades": 2,
    "stake_currency": "USDT",
    "stake_amount": 1.0,
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": False,
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
        "listen_port": int(os.environ.get("PORT", 8080)),
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
    "strategy": "ActiveTrader",
    "stoploss": -0.05
}

# Write config file
os.makedirs("config", exist_ok=True)
with open("config/config.json", "w") as f:
    json.dump(config, f, indent=2)

print("Config created successfully!")
print(f"Exchange key configured: {'Yes' if config['exchange']['key'] else 'No'}")

# Copy strategies to user_data
os.makedirs("user_data/strategies", exist_ok=True)
os.system("cp strategies/*.py user_data/strategies/ 2>/dev/null || true")

# Start freqtrade
print(f"Starting freqtrade on port {config['api_server']['listen_port']}...")
result = subprocess.run([
    "freqtrade", "trade",
    "--config", "config/config.json",
    "--strategy", "ActiveTrader",
    "--userdir", "user_data"
])
sys.exit(result.returncode)
