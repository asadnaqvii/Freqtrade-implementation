#!/bin/bash

# Quick Start Script for Crypto Investment Platform
# This script automates the entire setup process

set -e

# Colors
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
BLUE='\033[0;34m'
NC='\033[0m'

clear

echo -e "${CYAN}"
cat << "EOF"
╔═══════════════════════════════════════════════════════════╗
║                                                           ║
║     Crypto Investment Platform - Quick Start             ║
║     Automated Trading with Freqtrade                     ║
║                                                           ║
╚═══════════════════════════════════════════════════════════╝
EOF
echo -e "${NC}"

echo ""
echo -e "${YELLOW}This script will:${NC}"
echo "  1. Install all dependencies"
echo "  2. Set up the project structure"
echo "  3. Guide you through configuration"
echo "  4. Help you start trading in dry-run mode"
echo ""
echo -e "${RED}IMPORTANT: Only invest money you can afford to lose!${NC}"
echo ""

read -p "Press Enter to continue or Ctrl+C to cancel..."

# Step 1: Run setup
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Step 1: Installing Dependencies                          ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

if [ ! -f "setup.sh" ]; then
    echo -e "${RED}Error: setup.sh not found!${NC}"
    exit 1
fi

chmod +x setup.sh
./setup.sh

# Step 2: Activate environment
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Step 2: Activating Virtual Environment                   ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""

source venv/bin/activate

# Step 3: Configuration
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Step 3: Configuration Wizard                              ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${YELLOW}Starting interactive configuration wizard...${NC}"
echo ""

python config_wizard.py

# Step 4: Verify configuration
if [ ! -f "config/config.json" ]; then
    echo -e "${RED}Configuration failed or was cancelled.${NC}"
    exit 1
fi

# Step 5: Offer to run backtest
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Step 4: Testing (Optional but Recommended)               ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo "Would you like to run a backtest to see how your strategy"
echo "would have performed with historical data?"
echo ""
read -p "Run backtest? (yes/no) [yes]: " run_backtest
run_backtest=${run_backtest:-yes}

if [ "$run_backtest" = "yes" ]; then
    chmod +x scripts/backtest.sh
    ./scripts/backtest.sh
fi

# Step 6: Start bot
echo ""
echo -e "${CYAN}╔═══════════════════════════════════════════════════════════╗${NC}"
echo -e "${CYAN}║  Step 5: Starting Your Trading Bot                        ║${NC}"
echo -e "${CYAN}╚═══════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}Setup complete!${NC}"
echo ""
echo "You can now:"
echo ""
echo -e "${YELLOW}Option 1: Start in DRY-RUN mode (Recommended)${NC}"
echo "  - Uses virtual money"
echo "  - No risk"
echo "  - Practice and learn"
echo "  Command: ${GREEN}./start_bot.sh --dry-run${NC}"
echo ""
echo -e "${YELLOW}Option 2: Start in LIVE mode${NC}"
echo "  - Uses real money"
echo "  - Real trades"
echo "  - Only after testing!"
echo "  Command: ${RED}./start_bot.sh --live${NC}"
echo ""
echo -e "${YELLOW}Option 3: Monitor bot performance${NC}"
echo "  Command: ${GREEN}./scripts/monitor.sh${NC}"
echo "  Or visit: ${GREEN}http://localhost:8080${NC}"
echo ""

read -p "Start bot in dry-run mode now? (yes/no) [yes]: " start_now
start_now=${start_now:-yes}

if [ "$start_now" = "yes" ]; then
    echo ""
    echo -e "${GREEN}Starting bot in DRY-RUN mode...${NC}"
    echo -e "${YELLOW}Press Ctrl+C to stop the bot${NC}"
    echo ""
    chmod +x start_bot.sh
    ./start_bot.sh --dry-run
else
    echo ""
    echo -e "${GREEN}All set!${NC}"
    echo ""
    echo "When you're ready to start trading:"
    echo "  ${GREEN}./start_bot.sh --dry-run${NC}"
    echo ""
    echo "For help:"
    echo "  - Read docs/GETTING_STARTED.md"
    echo "  - Read docs/FAQ.md"
    echo "  - Check README.md"
    echo ""
    echo -e "${CYAN}Happy trading! 🚀${NC}"
fi
