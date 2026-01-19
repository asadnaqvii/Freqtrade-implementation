#!/bin/bash

set -e

echo "================================================"
echo "   Crypto Investment Platform Setup"
echo "   Powered by Freqtrade"
echo "================================================"
echo ""

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Check if Python is installed
echo -e "${YELLOW}Checking prerequisites...${NC}"
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}Python 3 is not installed. Please install Python 3.8 or higher.${NC}"
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo -e "${GREEN}✓ Python $PYTHON_VERSION found${NC}"

# Create directory structure
echo ""
echo -e "${YELLOW}Creating directory structure...${NC}"
mkdir -p config/templates
mkdir -p config/strategies
mkdir -p strategies/beginner
mkdir -p strategies/intermediate
mkdir -p strategies/custom
mkdir -p scripts
mkdir -p user_data/data
mkdir -p user_data/logs
mkdir -p user_data/backtest_results
mkdir -p docs
echo -e "${GREEN}✓ Directories created${NC}"

# Create virtual environment
echo ""
echo -e "${YELLOW}Creating Python virtual environment...${NC}"
if [ ! -d "venv" ]; then
    python3 -m venv venv
    echo -e "${GREEN}✓ Virtual environment created${NC}"
else
    echo -e "${GREEN}✓ Virtual environment already exists${NC}"
fi

# Activate virtual environment
source venv/bin/activate

# Upgrade pip
echo ""
echo -e "${YELLOW}Upgrading pip...${NC}"
pip install --upgrade pip

# Install Freqtrade
echo ""
echo -e "${YELLOW}Installing Freqtrade and dependencies...${NC}"
echo -e "${YELLOW}This may take a few minutes...${NC}"

pip install freqtrade[all]

echo -e "${GREEN}✓ Freqtrade installed successfully${NC}"

# Install additional useful packages
echo ""
echo -e "${YELLOW}Installing additional packages...${NC}"
pip install pandas numpy ta-lib requests python-telegram-bot colorama tabulate

echo -e "${GREEN}✓ Additional packages installed${NC}"

# Make scripts executable
echo ""
echo -e "${YELLOW}Setting up scripts...${NC}"
chmod +x scripts/*.sh 2>/dev/null || true
chmod +x *.sh 2>/dev/null || true
echo -e "${GREEN}✓ Scripts configured${NC}"

# Summary
echo ""
echo "================================================"
echo -e "${GREEN}Installation Complete!${NC}"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Activate the virtual environment:"
echo "   source venv/bin/activate"
echo ""
echo "2. Run the configuration wizard:"
echo "   python config_wizard.py"
echo ""
echo "3. Start the bot in dry-run mode (recommended):"
echo "   ./start_bot.sh --dry-run"
echo ""
echo "For help, see README.md or docs/GETTING_STARTED.md"
echo ""
