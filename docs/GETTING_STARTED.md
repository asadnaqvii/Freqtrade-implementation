# Getting Started Guide

Welcome to the Crypto Investment Platform! This guide will walk you through everything you need to know to start automated crypto trading.

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Installation](#installation)
3. [Configuration](#configuration)
4. [Testing Your Strategy](#testing-your-strategy)
5. [Going Live](#going-live)
6. [Monitoring Your Bot](#monitoring-your-bot)
7. [Troubleshooting](#troubleshooting)

## Prerequisites

Before you begin, make sure you have:

- **Python 3.8 or higher** installed
- **Basic understanding** of cryptocurrency trading
- **Exchange account** (Binance, Coinbase, etc.)
- **API keys** from your exchange (for live trading)
- **Starting capital** (recommend at least $100-500 for testing)

## Installation

### Step 1: Clone the Repository

```bash
git clone <your-repo-url>
cd Freqtrade-implementatiom
```

### Step 2: Run the Setup Script

```bash
chmod +x setup.sh
./setup.sh
```

This will:
- Create a Python virtual environment
- Install Freqtrade and all dependencies
- Set up the project structure
- Make all scripts executable

The installation may take 5-10 minutes.

### Step 3: Activate Virtual Environment

```bash
source venv/bin/activate
```

You'll need to activate the virtual environment every time you work with the bot.

## Configuration

### Interactive Configuration Wizard

The easiest way to configure your bot is using the interactive wizard:

```bash
python config_wizard.py
```

The wizard will guide you through:

1. **Trading Mode**
   - Choose dry-run (virtual money) or live (real money)
   - **Always start with dry-run!**

2. **Exchange Selection**
   - Select your cryptocurrency exchange
   - Popular choices: Binance, Coinbase Pro, Kraken

3. **API Keys**
   - Enter your exchange API keys
   - Optional for dry-run mode
   - **Never share your API keys!**

4. **Trading Pairs**
   - Select which cryptocurrencies to trade
   - Default: BTC/USDT, ETH/USDT, BNB/USDT, ADA/USDT, SOL/USDT

5. **Strategy Selection**
   - **ConservativeRSI**: Best for beginners (low risk)
   - **EMACrossover**: Medium risk, follows trends
   - **BollingerBreakout**: Medium-high risk, volatility-based

6. **Risk Management**
   - Stake amount: How much to invest per trade
   - Max open trades: How many trades at once
   - Stop loss: Maximum loss per trade

7. **Notifications**
   - Optional Telegram notifications
   - Get alerts for trades and important events

### Manual Configuration

If you prefer to configure manually, edit `config/config.json`:

```json
{
  "dry_run": true,
  "stake_amount": 100,
  "max_open_trades": 3,
  "strategy": "ConservativeRSI",
  "stoploss": -0.05,
  "exchange": {
    "name": "binance",
    "key": "your-api-key",
    "secret": "your-api-secret",
    "pair_whitelist": ["BTC/USDT", "ETH/USDT"]
  }
}
```

## Testing Your Strategy

**ALWAYS test before going live!** Here's how:

### 1. Backtesting

Test your strategy with historical data:

```bash
chmod +x scripts/backtest.sh
./scripts/backtest.sh
```

This will:
- Download historical price data
- Run your strategy against past market conditions
- Show performance metrics (profit, win rate, etc.)

**What to look for:**
- Total profit > 0%
- Win rate > 50%
- Maximum drawdown < 20%
- Consistent performance across different time periods

### 2. Dry-Run Mode

Test with real-time market data but virtual money:

```bash
chmod +x start_bot.sh
./start_bot.sh --dry-run
```

**Let it run for at least 1-2 weeks** to see how it performs in real market conditions.

### 3. Monitor Performance

Watch your bot in action:

```bash
chmod +x scripts/monitor.sh
./scripts/monitor.sh
```

Or visit the web dashboard at: **http://localhost:8080**
- Username: `freqtrader`
- Password: `freqtrader`

## Going Live

**Only go live after:**
- ✓ Backtesting shows positive results
- ✓ Dry-run performs well for 1-2 weeks
- ✓ You understand the strategy and risks
- ✓ You're prepared to lose your investment

### Starting Live Trading

1. **Set up API keys** (if not already done):
   - Log into your exchange
   - Create API key with trading permissions
   - **Disable withdrawal permissions** (security)
   - Add API key to `config/config.json`

2. **Set dry_run to false** in config:
   ```json
   "dry_run": false
   ```

3. **Start small**:
   - Begin with minimum stake amounts
   - Use only 10-20% of your trading capital
   - Increase gradually as you gain confidence

4. **Start the bot**:
   ```bash
   ./start_bot.sh --live
   ```

5. **Monitor closely**:
   - Check multiple times per day initially
   - Review all trades
   - Adjust settings if needed

## Monitoring Your Bot

### Web Dashboard

Access at **http://localhost:8080**

Features:
- View open trades
- See profit/loss in real-time
- Monitor strategy performance
- Check trade history
- View market data

### Command Line Monitor

```bash
./scripts/monitor.sh
```

Shows:
- Bot status
- Current open trades
- Performance statistics
- Recent log entries
- Auto-refreshes every 30 seconds

### Log Files

Detailed logs are saved in `user_data/logs/freqtrade.log`:

```bash
tail -f user_data/logs/freqtrade.log
```

### Telegram Notifications (Optional)

Get instant alerts on your phone:

1. Create a Telegram bot:
   - Search for @BotFather on Telegram
   - Send `/newbot` and follow instructions
   - Save your bot token

2. Get your chat ID:
   - Search for @userinfobot on Telegram
   - Start a chat to get your ID

3. Configure in wizard or config file

## Troubleshooting

### Bot Won't Start

**Check:**
- Virtual environment is activated: `source venv/bin/activate`
- Config file exists: `ls -la config/config.json`
- API keys are correct (for live mode)

**Fix:**
```bash
./setup.sh  # Reinstall if needed
python config_wizard.py  # Reconfigure
```

### No Trades Being Made

**Possible reasons:**
- Market conditions don't meet strategy criteria
- Insufficient balance
- API keys don't have trading permissions
- Trading pairs have low volume

**Check:**
- View logs: `tail -f user_data/logs/freqtrade.log`
- Verify balance on exchange
- Check market conditions match strategy requirements

### Losing Money in Live Trading

**Immediate actions:**
1. **Stop the bot**: Press Ctrl+C
2. **Review trades**: Check what went wrong
3. **Switch to dry-run**: Test more before going live again
4. **Adjust strategy**: Modify risk parameters

**Prevention:**
- Always backtest thoroughly
- Start with small amounts
- Use appropriate stop losses
- Don't overtrade
- Be patient

### API Errors

**Common issues:**
- Invalid API keys: Double-check in exchange settings
- IP restrictions: Add your IP to allowed list
- Rate limiting: Bot is making too many requests
- Insufficient permissions: Enable trading for API key

**Fix:**
- Regenerate API keys
- Check exchange API status
- Reduce polling frequency in config

## Best Practices

1. **Start Conservative**
   - Use ConservativeRSI strategy
   - Small stake amounts
   - Dry-run for 2+ weeks

2. **Diversify**
   - Trade multiple pairs
   - Don't put all capital in one strategy
   - Consider multiple bots with different strategies

3. **Monitor Regularly**
   - Check at least daily
   - Review performance weekly
   - Adjust as needed

4. **Keep Learning**
   - Understand why trades win/lose
   - Study market conditions
   - Improve strategies over time

5. **Risk Management**
   - Never invest more than you can afford to lose
   - Use stop losses always
   - Set maximum daily/weekly loss limits

## Next Steps

- Read [STRATEGIES.md](STRATEGIES.md) to understand each strategy
- Check [FAQ.md](FAQ.md) for common questions
- Join the community for support
- Experiment with custom strategies

## Getting Help

- Check logs: `user_data/logs/freqtrade.log`
- Visit Freqtrade docs: https://www.freqtrade.io
- Open an issue on GitHub
- Ask in community Discord/Telegram

Happy trading! 🚀
