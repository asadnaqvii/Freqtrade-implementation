#!/bin/bash

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Function to display header
show_header() {
    clear
    echo -e "${CYAN}================================================${NC}"
    echo -e "${CYAN}   Crypto Investment Platform Monitor${NC}"
    echo -e "${CYAN}   $(date '+%Y-%m-%d %H:%M:%S')${NC}"
    echo -e "${CYAN}================================================${NC}"
    echo ""
}

# Function to check if bot is running
check_bot_status() {
    if pgrep -f "freqtrade trade" > /dev/null; then
        echo -e "${GREEN}✓ Bot Status: RUNNING${NC}"
        return 0
    else
        echo -e "${RED}✗ Bot Status: NOT RUNNING${NC}"
        return 1
    fi
}

# Function to show current trades
show_current_trades() {
    echo ""
    echo -e "${YELLOW}Current Open Trades:${NC}"
    echo "-------------------"

    if [ -f "user_data/tradesv3.sqlite" ]; then
        # Activate virtual environment
        source venv/bin/activate 2>/dev/null

        # Use freqtrade to show current status
        freqtrade show_trades --config config/config.json --db-url sqlite:///user_data/tradesv3.sqlite 2>/dev/null || \
            echo "No trades data available yet"
    else
        echo "No trades database found. Start the bot first."
    fi
}

# Function to show performance stats
show_performance() {
    echo ""
    echo -e "${YELLOW}Performance Statistics:${NC}"
    echo "----------------------"

    if [ -f "user_data/tradesv3.sqlite" ]; then
        source venv/bin/activate 2>/dev/null

        # Show profit stats
        freqtrade profit --config config/config.json --db-url sqlite:///user_data/tradesv3.sqlite 2>/dev/null || \
            echo "No performance data available yet"
    else
        echo "No trades database found. Start the bot first."
    fi
}

# Function to show recent log entries
show_logs() {
    echo ""
    echo -e "${YELLOW}Recent Log Entries:${NC}"
    echo "-------------------"

    if [ -f "user_data/logs/freqtrade.log" ]; then
        tail -n 10 user_data/logs/freqtrade.log
    else
        echo "No log file found yet"
    fi
}

# Main monitoring loop
main() {
    while true; do
        show_header

        check_bot_status
        bot_running=$?

        if [ $bot_running -eq 0 ]; then
            show_current_trades
            show_performance
            show_logs
        else
            echo ""
            echo "To start the bot, run:"
            echo "  ./start_bot.sh --dry-run   (for practice mode)"
            echo "  ./start_bot.sh --live      (for live trading)"
        fi

        echo ""
        echo -e "${CYAN}Web Dashboard: ${GREEN}http://localhost:8080${NC}"
        echo ""
        echo "Press 'q' to quit, or wait 30 seconds for auto-refresh..."

        # Wait for input with timeout
        read -t 30 -n 1 key

        if [ "$key" = "q" ] || [ "$key" = "Q" ]; then
            echo ""
            echo "Exiting monitor..."
            break
        fi
    done
}

# Check if we're in the right directory
if [ ! -f "config/config.json" ]; then
    echo -e "${RED}Error: Not in project directory or config not found${NC}"
    exit 1
fi

main
