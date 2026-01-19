#!/bin/bash

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================================${NC}"
echo -e "${CYAN}   Starting Crypto Investment Platform${NC}"
echo -e "${CYAN}================================================${NC}"
echo ""

# Check if config exists
if [ ! -f "config/config.json" ]; then
    echo -e "${RED}Error: Configuration file not found!${NC}"
    echo ""
    echo "Please run the configuration wizard first:"
    echo "  python config_wizard.py"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo -e "${RED}Error: Virtual environment not found!${NC}"
    echo ""
    echo "Please run setup first:"
    echo "  ./setup.sh"
    exit 1
fi

# Activate virtual environment
source venv/bin/activate

# Parse command line arguments
MODE="dry_run"
if [ "$1" == "--live" ]; then
    MODE="live"
    echo -e "${RED}WARNING: Starting in LIVE mode with REAL money!${NC}"
    read -p "Are you sure? (yes/no): " confirm
    if [ "$confirm" != "yes" ]; then
        echo "Cancelled."
        exit 0
    fi
elif [ "$1" == "--dry-run" ] || [ "$1" == "" ]; then
    MODE="dry_run"
    echo -e "${GREEN}Starting in DRY-RUN mode (virtual money)${NC}"
else
    echo -e "${RED}Invalid argument. Use --dry-run or --live${NC}"
    exit 1
fi

# Create necessary directories
mkdir -p user_data/logs
mkdir -p user_data/data

# Start the bot
echo ""
echo -e "${YELLOW}Starting Freqtrade...${NC}"
echo ""
echo -e "${CYAN}Monitor your bot at: ${GREEN}http://localhost:8080${NC}"
echo -e "${CYAN}Username: ${GREEN}freqtrader${NC}"
echo -e "${CYAN}Password: ${GREEN}freqtrader${NC}"
echo ""
echo -e "${YELLOW}Press Ctrl+C to stop the bot${NC}"
echo ""

# Run freqtrade
freqtrade trade \
    --config config/config.json \
    --strategy-path strategies \
    --datadir user_data/data \
    --logfile user_data/logs/freqtrade.log \
    --db-url sqlite:///user_data/tradesv3.sqlite
