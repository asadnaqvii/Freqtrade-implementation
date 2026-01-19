#!/usr/bin/env python3
"""
Interactive Configuration Wizard for Crypto Investment Platform
Makes it easy for beginners to configure Freqtrade
"""

import json
import os
import sys
from pathlib import Path

try:
    from colorama import Fore, Style, init
    init(autoreset=True)
except ImportError:
    # Fallback if colorama is not installed
    class Fore:
        GREEN = YELLOW = RED = CYAN = BLUE = ""
    class Style:
        BRIGHT = RESET_ALL = ""

def print_header(text):
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{'=' * 60}")
    print(f"{text:^60}")
    print(f"{'=' * 60}{Style.RESET_ALL}\n")

def print_success(text):
    print(f"{Fore.GREEN}✓ {text}{Style.RESET_ALL}")

def print_warning(text):
    print(f"{Fore.YELLOW}⚠ {text}{Style.RESET_ALL}")

def print_error(text):
    print(f"{Fore.RED}✗ {text}{Style.RESET_ALL}")

def get_input(prompt, default=None, options=None):
    """Get user input with optional default and validation"""
    if default:
        prompt_text = f"{prompt} [{default}]: "
    else:
        prompt_text = f"{prompt}: "

    if options:
        print(f"Options: {', '.join(options)}")

    while True:
        value = input(prompt_text).strip()
        if not value and default:
            return default
        if not value:
            print_error("This field is required!")
            continue
        if options and value not in options:
            print_error(f"Please choose from: {', '.join(options)}")
            continue
        return value

def get_yes_no(prompt, default="yes"):
    """Get yes/no input"""
    value = get_input(f"{prompt} (yes/no)", default, ["yes", "no"])
    return value.lower() == "yes"

def main():
    print_header("Crypto Investment Platform - Configuration Wizard")

    print("This wizard will help you configure your trading bot.")
    print("You can change these settings later in config/config.json\n")

    config = {}

    # Trading Mode
    print_header("1. Trading Mode")
    print("Dry-run mode uses virtual money (safe for beginners)")
    print("Live mode uses real money (only use after testing!)")
    dry_run = get_yes_no("Start with dry-run mode? (RECOMMENDED)", "yes")
    config['dry_run'] = dry_run

    if not dry_run:
        print_warning("You've chosen LIVE mode. Make sure you understand the risks!")
        confirm = get_yes_no("Are you sure you want to use real money?", "no")
        if not confirm:
            config['dry_run'] = True
            print_success("Switched to dry-run mode (safe choice!)")

    # Exchange Selection
    print_header("2. Exchange Selection")
    print("Choose your cryptocurrency exchange:")
    print("1. Binance (recommended for beginners)")
    print("2. Coinbase Pro")
    print("3. Kraken")
    print("4. Bitfinex")
    print("5. Other")

    exchange_choice = get_input("Select exchange (1-5)", "1", ["1", "2", "3", "4", "5"])

    exchange_map = {
        "1": "binance",
        "2": "coinbasepro",
        "3": "kraken",
        "4": "bitfinex",
        "5": get_input("Enter exchange name")
    }

    exchange_name = exchange_map[exchange_choice]
    config['exchange'] = {
        'name': exchange_name,
        'key': '',
        'secret': '',
        'ccxt_config': {},
        'ccxt_async_config': {}
    }

    # API Keys
    print_header("3. API Keys")
    if dry_run:
        print("In dry-run mode, API keys are optional (can leave blank)")
    else:
        print_warning("For live trading, you MUST enter valid API keys!")

    print(f"\nTo get API keys from {exchange_name}:")
    print(f"1. Log into your {exchange_name} account")
    print("2. Go to API Management section")
    print("3. Create a new API key with trading permissions")
    print("4. Copy the key and secret")

    if get_yes_no("\nDo you have API keys ready?", "no"):
        config['exchange']['key'] = get_input("Enter API Key")
        config['exchange']['secret'] = get_input("Enter API Secret")
        print_success("API keys configured")
    else:
        print_warning("You can add API keys later in config/config.json")

    # Trading Pairs
    print_header("4. Trading Pairs")
    print("Select which cryptocurrencies to trade:")
    print("Examples: BTC/USDT, ETH/USDT, BNB/USDT")

    default_pairs = ["BTC/USDT", "ETH/USDT", "BNB/USDT", "ADA/USDT", "SOL/USDT"]
    print(f"\nDefault pairs: {', '.join(default_pairs)}")

    if get_yes_no("Use default trading pairs?", "yes"):
        config['exchange']['pair_whitelist'] = default_pairs
    else:
        pairs = []
        print("Enter trading pairs (one per line, empty line to finish):")
        while True:
            pair = input("> ").strip()
            if not pair:
                break
            pairs.append(pair)
        config['exchange']['pair_whitelist'] = pairs if pairs else default_pairs

    config['exchange']['pair_blacklist'] = []

    # Strategy Selection
    print_header("5. Trading Strategy")
    print("Choose a trading strategy:")
    print("1. Conservative RSI (Low Risk - Good for beginners)")
    print("2. EMA Crossover (Medium Risk - Trend following)")
    print("3. Bollinger Bands (Medium-High Risk - Volatility)")

    strategy_choice = get_input("Select strategy (1-3)", "1", ["1", "2", "3"])

    strategy_map = {
        "1": "ConservativeRSI",
        "2": "EMACrossover",
        "3": "BollingerBreakout"
    }

    config['strategy'] = strategy_map[strategy_choice]

    # Risk Management
    print_header("6. Risk Management")

    print("Stake amount: How much to invest per trade")
    stake_amount = float(get_input("Stake amount (e.g., 100)", "100"))

    config['stake_currency'] = 'USDT'
    config['stake_amount'] = stake_amount

    print("\nMaximum open trades (how many trades at once)")
    max_trades = int(get_input("Max open trades", "3"))
    config['max_open_trades'] = max_trades

    # Stop Loss
    print("\nStop loss: Automatically sell if trade loses X%")
    stoploss = float(get_input("Stop loss percentage (e.g., 5 for 5%)", "5"))
    config['stoploss'] = -abs(stoploss) / 100

    # Notifications
    print_header("7. Notifications")

    enable_telegram = get_yes_no("Enable Telegram notifications?", "no")

    if enable_telegram:
        print("\nTo set up Telegram notifications:")
        print("1. Search for @BotFather on Telegram")
        print("2. Create a new bot and get your token")
        print("3. Search for @userinfobot to get your chat ID")

        config['telegram'] = {
            'enabled': True,
            'token': get_input("Enter Telegram bot token"),
            'chat_id': get_input("Enter your Telegram chat ID")
        }
    else:
        config['telegram'] = {'enabled': False}

    # Build complete configuration
    full_config = {
        'max_open_trades': config['max_open_trades'],
        'stake_currency': config['stake_currency'],
        'stake_amount': config['stake_amount'],
        'tradable_balance_ratio': 0.99,
        'fiat_display_currency': 'USD',
        'dry_run': config['dry_run'],
        'cancel_open_orders_on_exit': False,

        'unfilledtimeout': {
            'entry': 10,
            'exit': 10,
            'exit_timeout_count': 0,
            'unit': 'minutes'
        },

        'entry_pricing': {
            'price_side': 'same',
            'use_order_book': True,
            'order_book_top': 1,
            'price_last_balance': 0.0,
            'check_depth_of_market': {
                'enabled': False,
                'bids_to_ask_delta': 1
            }
        },

        'exit_pricing': {
            'price_side': 'same',
            'use_order_book': True,
            'order_book_top': 1
        },

        'exchange': config['exchange'],

        'pairlists': [
            {
                'method': 'StaticPairList'
            }
        ],

        'edge': {
            'enabled': False
        },

        'telegram': config['telegram'],

        'api_server': {
            'enabled': True,
            'listen_ip_address': '127.0.0.1',
            'listen_port': 8080,
            'verbosity': 'error',
            'enable_openapi': False,
            'jwt_secret_key': os.urandom(32).hex(),
            'CORS_origins': [],
            'username': 'freqtrader',
            'password': 'freqtrader'
        },

        'bot_name': 'freqtrade',
        'initial_state': 'running',
        'force_entry_enable': False,
        'internals': {
            'process_throttle_secs': 5
        },

        'strategy': config['strategy'],
        'stoploss': config['stoploss']
    }

    # Save configuration
    print_header("Saving Configuration")

    config_dir = Path('config')
    config_dir.mkdir(exist_ok=True)

    config_file = config_dir / 'config.json'

    with open(config_file, 'w') as f:
        json.dump(full_config, f, indent=4)

    print_success(f"Configuration saved to {config_file}")

    # Summary
    print_header("Configuration Summary")
    print(f"Trading Mode: {'DRY-RUN (Virtual Money)' if config['dry_run'] else 'LIVE (Real Money)'}")
    print(f"Exchange: {config['exchange']['name']}")
    print(f"Trading Pairs: {', '.join(config['exchange']['pair_whitelist'][:3])}...")
    print(f"Strategy: {config['strategy']}")
    print(f"Stake Amount: {config['stake_amount']} {config['stake_currency']}")
    print(f"Max Open Trades: {config['max_open_trades']}")
    print(f"Stop Loss: {abs(config['stoploss']) * 100}%")
    print(f"Telegram: {'Enabled' if config['telegram']['enabled'] else 'Disabled'}")

    print_header("Next Steps")
    print("1. Review your configuration in config/config.json")
    print("2. Start the bot:")
    if config['dry_run']:
        print("   ./start_bot.sh --dry-run")
    else:
        print("   ./start_bot.sh --live")
    print("\n3. Monitor your bot at http://localhost:8080")
    print("\nHappy trading! 🚀")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nConfiguration cancelled.")
        sys.exit(0)
    except Exception as e:
        print_error(f"Error: {e}")
        sys.exit(1)
