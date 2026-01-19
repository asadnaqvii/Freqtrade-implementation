#!/bin/bash

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}================================================${NC}"
echo -e "${CYAN}   Strategy Backtesting${NC}"
echo -e "${CYAN}================================================${NC}"
echo ""

# Check if config exists
if [ ! -f "config/config.json" ]; then
    echo -e "${RED}Error: Configuration file not found!${NC}"
    echo "Please run: python config_wizard.py"
    exit 1
fi

# Activate virtual environment
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo -e "${RED}Error: Virtual environment not found!${NC}"
    echo "Please run: ./setup.sh"
    exit 1
fi

# Get strategy name
if [ -z "$1" ]; then
    echo "Available strategies:"
    echo "1. ConservativeRSI (Low Risk)"
    echo "2. EMACrossover (Medium Risk)"
    echo "3. BollingerBreakout (Medium-High Risk)"
    echo ""
    read -p "Select strategy (1-3): " choice

    case $choice in
        1) STRATEGY="ConservativeRSI" ;;
        2) STRATEGY="EMACrossover" ;;
        3) STRATEGY="BollingerBreakout" ;;
        *) echo -e "${RED}Invalid choice${NC}"; exit 1 ;;
    esac
else
    STRATEGY="$1"
fi

# Get time range
echo ""
echo "Select time range for backtest:"
echo "1. Last 7 days"
echo "2. Last 30 days"
echo "3. Last 90 days"
echo "4. Custom range"
read -p "Select (1-4): " range_choice

case $range_choice in
    1) TIMERANGE="$(date -d '7 days ago' +%Y%m%d)-" ;;
    2) TIMERANGE="$(date -d '30 days ago' +%Y%m%d)-" ;;
    3) TIMERANGE="$(date -d '90 days ago' +%Y%m%d)-" ;;
    4)
        read -p "Start date (YYYYMMDD): " start_date
        read -p "End date (YYYYMMDD, leave empty for today): " end_date
        if [ -z "$end_date" ]; then
            TIMERANGE="${start_date}-"
        else
            TIMERANGE="${start_date}-${end_date}"
        fi
        ;;
    *) echo -e "${RED}Invalid choice${NC}"; exit 1 ;;
esac

echo ""
echo -e "${YELLOW}Downloading historical data...${NC}"

# Download data first
freqtrade download-data \
    --config config/config.json \
    --datadir user_data/data \
    --timerange "$TIMERANGE" \
    --timeframe 5m 15m 1h

echo ""
echo -e "${YELLOW}Running backtest for $STRATEGY...${NC}"
echo ""

# Run backtest
freqtrade backtesting \
    --config config/config.json \
    --strategy "$STRATEGY" \
    --strategy-path strategies \
    --datadir user_data/data \
    --timerange "$TIMERANGE" \
    --export trades \
    --export-filename user_data/backtest_results/${STRATEGY}_$(date +%Y%m%d_%H%M%S).json

echo ""
echo -e "${GREEN}Backtest completed!${NC}"
echo ""
echo "Results saved in user_data/backtest_results/"
echo ""
echo "To view detailed results, run:"
echo "  freqtrade plot-dataframe --strategy $STRATEGY --config config/config.json"
