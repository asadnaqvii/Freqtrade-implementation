#!/usr/bin/env python3
"""
Railway startup script — Freqtrade + strategy config helper.
Runs Freqtrade as the main process with a small background thread
that allows the WhatsApp bot to update config.json (strategy field)
before calling reload_config on Freqtrade.
"""
import os
import sys
import json
import subprocess
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

print("=== Railway Start Script ===")
print(f"Python: {sys.version}")
print(f"HOME: {os.path.expanduser('~')}")
print(f"PWD: {os.getcwd()}")

# Set TA-Lib library path for runtime
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

# Verify TA-Lib
try:
    import talib
    print(f"TA-Lib loaded (version: {talib.__version__})")
except ImportError as e:
    print(f"WARNING: TA-Lib import failed: {e}")

# Validate exchange env vars
required_vars = ["FREQTRADE__EXCHANGE__KEY", "FREQTRADE__EXCHANGE__SECRET", "FREQTRADE__EXCHANGE__PASSWORD"]
missing = [v for v in required_vars if not os.environ.get(v)]
if missing:
    print(f"WARNING: Missing: {', '.join(missing)}")

port = int(os.environ.get("PORT", 10000))
CONFIG_PATH = "config/config.json"

# Available strategies (must match strategies/*.py)
VALID_STRATEGIES = [
    "ConservativeRSI",
    "EMACrossover",
    "BollingerBreakout",
    "ActiveTrader",
    "MeanReversionScalper",
    "EMACrossoverScalper",
]

# ── Build config.json ─────────────────────────────────────
config = {
    "max_open_trades": 6,
    "stake_currency": "USDT",
    "stake_amount": 1.0,
    "tradable_balance_ratio": 0.99,
    "fiat_display_currency": "USD",
    "dry_run": os.environ.get("DRY_RUN", "false").lower() == "true",
    "dry_run_wallet": 1000,
    "cancel_open_orders_on_exit": False,
    "unfilledtimeout": {
        "entry": 10, "exit": 10,
        "exit_timeout_count": 0, "unit": "minutes"
    },
    "entry_pricing": {
        "price_side": "same", "use_order_book": True,
        "order_book_top": 1, "price_last_balance": 0.0,
        "check_depth_of_market": {"enabled": False, "bids_to_ask_delta": 1}
    },
    "exit_pricing": {
        "price_side": "same", "use_order_book": True, "order_book_top": 1
    },
    "exchange": {
        "name": "kucoin",
        "key": os.environ.get("FREQTRADE__EXCHANGE__KEY", ""),
        "secret": os.environ.get("FREQTRADE__EXCHANGE__SECRET", ""),
        "password": os.environ.get("FREQTRADE__EXCHANGE__PASSWORD", ""),
        "ccxt_config": {},
        "ccxt_async_config": {"aiohttp_trust_env": True},
        "pair_whitelist": ["BTC/USDT", "ETH/USDT", "SOL/USDT", "ADA/USDT"],
        "pair_blacklist": []
    },
    "pairlists": [{"method": "StaticPairList"}],
    "edge": {"enabled": False},
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
    "internals": {"process_throttle_secs": 5},
    "strategy": os.environ.get("STRATEGY", "MeanReversionScalper"),
    "stoploss": -0.004
}

os.makedirs("config", exist_ok=True)
with open(CONFIG_PATH, "w") as f:
    json.dump(config, f, indent=2)

print(f"Config created: strategy={config['strategy']}, dry_run={config['dry_run']}, port={port}")

# Copy strategies to user_data
os.makedirs("user_data/strategies", exist_ok=True)
os.system("cp strategies/*.py user_data/strategies/ 2>/dev/null || true")


# ── Strategy Update Helper (runs on port+1) ──────────────
# The WhatsApp bot calls POST /set_strategy with {"strategy": "Name"}
# This updates config.json so reload_config picks up the new strategy.

class StrategyHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path == "/set_strategy":
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length)) if length else {}
            strategy_name = body.get("strategy", "")

            if strategy_name not in VALID_STRATEGIES:
                self.send_response(400)
                self.end_headers()
                self.wfile.write(json.dumps({
                    "error": f"Invalid strategy. Valid: {VALID_STRATEGIES}"
                }).encode())
                return

            # Read current config, update strategy, write back
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            cfg["strategy"] = strategy_name
            with open(CONFIG_PATH, "w") as f:
                json.dump(cfg, f, indent=2)

            print(f"[StrategyHelper] Config updated: strategy={strategy_name}")
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"status": "ok", "strategy": strategy_name}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self):
        if self.path == "/current_strategy":
            with open(CONFIG_PATH, "r") as f:
                cfg = json.load(f)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({"strategy": cfg.get("strategy")}).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        print(f"[StrategyHelper] {args[0]}")


def run_helper_server():
    helper_port = port + 1
    server = HTTPServer(("0.0.0.0", helper_port), StrategyHandler)
    print(f"Strategy helper running on port {helper_port}")
    server.serve_forever()


# Start helper in background thread
helper_thread = threading.Thread(target=run_helper_server, daemon=True)
helper_thread.start()


# ── Start Freqtrade (no --strategy flag!) ─────────────────
# Strategy comes from config.json, so reload_config can change it.
print(f"Starting freqtrade on port {port} with strategy {config['strategy']}...", flush=True)
sys.stdout.flush()

os.execvp("freqtrade", [
    "freqtrade", "trade",
    "--config", CONFIG_PATH,
    "--strategy-path", "strategies",
    "--userdir", "user_data"
])
