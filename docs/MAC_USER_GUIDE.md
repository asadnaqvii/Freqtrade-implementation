# Mac User Guide - Crypto Trading Bot

A simple, step-by-step guide for Mac users to run this automated crypto trading bot.

---

## What Can You Do With This Bot?

| Feature | Description |
|---------|-------------|
| **Practice Trading** | Use fake money to learn without risk (Dry-Run Mode) |
| **Backtest Strategies** | Test how strategies would have performed on past data |
| **Live Trading** | Trade with real money (only after practicing!) |
| **Monitor Performance** | Watch your bot through a web dashboard |
| **Choose Strategies** | Pick from 3 pre-built trading strategies |

---

## Prerequisites

Before starting, you need:

- **A Mac** running macOS 10.15 or later
- **Python 3.8+** (check with `python3 --version` in Terminal)
- **Internet connection**

### Don't have Python? Install it:

```bash
# Option 1: Using Homebrew (recommended)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
brew install python

# Option 2: Download from python.org
# Visit https://www.python.org/downloads/
```

---

## Quick Start (5 Minutes)

### Step 1: Open Terminal

Press `Cmd + Space`, type **Terminal**, press Enter.

### Step 2: Navigate to the Project

```bash
cd /path/to/freqtrade.io
```
*(Replace with your actual folder path)*

### Step 3: Run Setup

```bash
chmod +x setup.sh
./setup.sh
```

Wait 5-10 minutes for installation to complete.

### Step 4: Start the Bot in Practice Mode

```bash
./start_bot.sh
```

That's it! Your bot is now running with **virtual money**.

---

## Understanding Dry-Run Mode (Practice Mode)

**Dry-Run Mode is your safe playground.** Here's what happens:

| Aspect | Dry-Run (Practice) | Live (Real Money) |
|--------|-------------------|-------------------|
| Money Used | Virtual $1,000 | Your real crypto |
| Trades | Simulated | Actually executed |
| Risk | Zero | Real financial risk |
| Purpose | Learning & testing | Actual investing |

### What You'll See in Dry-Run Mode:

1. **Virtual Wallet**: You start with $1,000 in fake USDT
2. **Real Market Data**: Prices are real, only trades are simulated
3. **Full Experience**: Everything works exactly like live trading
4. **No Risk**: You can't lose real money

---

## Complete Command Reference

### Everyday Commands

Open Terminal and navigate to your project folder first:

```bash
cd /path/to/freqtrade.io
source venv/bin/activate
```

| What You Want | Command |
|---------------|---------|
| Start bot (practice mode) | `./start_bot.sh` |
| Start bot (real money) | `./start_bot.sh --live` |
| Stop the bot | Press `Ctrl + C` |
| View web dashboard | Open http://localhost:8080 |

### Testing & Analysis Commands

| What You Want | Command |
|---------------|---------|
| Test strategy on past data | `./scripts/backtest.sh` |
| Monitor bot status | `./scripts/monitor.sh` |
| View live logs | `tail -f user_data/logs/freqtrade.log` |

### Setup Commands

| What You Want | Command |
|---------------|---------|
| Initial setup | `./setup.sh` |
| Configure settings | `python config_wizard.py` |
| Activate environment | `source venv/bin/activate` |

---

## Web Dashboard Guide

Once your bot is running, open your browser to:

**http://localhost:8080**

Login with:
- **Username:** `freqtrader`
- **Password:** `freqtrader`

### What You'll See:

| Section | What It Shows |
|---------|---------------|
| **Dashboard** | Overview of profit/loss, open trades |
| **Trades** | List of all trades (open and closed) |
| **Performance** | Charts showing your returns over time |
| **Daily/Weekly** | Breakdown of performance by period |
| **Logs** | Real-time bot activity |

---

## Your Current Configuration

Your bot is pre-configured with these settings:

| Setting | Value | What It Means |
|---------|-------|---------------|
| Mode | Dry-Run | Practice with fake money |
| Virtual Balance | $1,000 USDT | Your starting practice money |
| Strategy | ConservativeRSI | Safest beginner strategy |
| Exchange | KuCoin | Where trades happen |
| Coins Traded | BTC, ETH, SOL, ADA | The cryptocurrencies it watches |
| Per Trade | $100 | Amount used per trade |
| Max Trades | 2 | Maximum simultaneous positions |
| Stop Loss | 5% | Maximum loss per trade |

---

## Step-by-Step: Your First Practice Session

### 1. Start the Bot

```bash
cd /path/to/freqtrade.io
./start_bot.sh
```

You'll see:
```
================================================
   Starting Crypto Investment Platform
================================================

Starting in DRY-RUN mode (virtual money)

Monitor your bot at: http://localhost:8080
Username: freqtrader
Password: freqtrader

Press Ctrl+C to stop the bot
```

### 2. Open the Dashboard

Open Safari/Chrome and go to: **http://localhost:8080**

### 3. Watch and Learn

- The bot will analyze the market every few minutes
- When conditions match the strategy, it will "buy"
- When exit conditions are met, it will "sell"
- All trades are simulated - no real money involved

### 4. Let It Run

- Leave it running for a few days
- Check the dashboard periodically
- See how the strategy performs

### 5. Stop When Done

Press `Ctrl + C` in Terminal to stop the bot.

---

## Understanding What the Bot Does

### The Trading Cycle (Every 5 Minutes)

```
1. Downloads latest prices from KuCoin
           ↓
2. Calculates indicators (RSI, moving averages)
           ↓
3. Checks: "Should I buy?" (RSI < 30 = oversold = good time to buy)
           ↓
4. If yes → Places virtual buy order
           ↓
5. Monitors position for exit signals
           ↓
6. Checks: "Should I sell?" (RSI > 70 = overbought = time to sell)
           ↓
7. If yes → Places virtual sell order
           ↓
8. Logs everything and updates dashboard
```

### Example Trade (What You Might See)

```
10:30 AM - BTC/USDT RSI dropped to 28 (oversold signal)
         → Bot BUYS $100 of BTC at $42,000
         → Sets stop loss at $39,900 (5% below)

2:45 PM  - BTC/USDT RSI rose to 72 (overbought signal)
         → Bot SELLS BTC at $43,200
         → Profit: $2.86 (2.86%)
```

---

## The Three Available Strategies

You can change your strategy in `config/config.json`:

### 1. ConservativeRSI (Current - Recommended for Beginners)

```
Risk: LOW
How it works: Buys when market is oversold, sells when overbought
Best for: Beginners, stable markets
```

### 2. EMACrossover

```
Risk: MEDIUM
How it works: Follows market trends using moving averages
Best for: Trending markets
```

### 3. BollingerBreakout

```
Risk: MEDIUM
How it works: Catches price bounces off volatility bands
Best for: Volatile markets
```

To change strategy, edit `config/config.json`:
```json
"strategy": "EMACrossover"
```

---

## Troubleshooting

### "Command not found" Error

```bash
# Make sure you're in the right folder
cd /path/to/freqtrade.io

# Activate the virtual environment
source venv/bin/activate
```

### "Permission denied" Error

```bash
chmod +x setup.sh start_bot.sh
chmod +x scripts/*.sh
```

### Bot Won't Start

```bash
# Re-run setup
./setup.sh

# Check if config exists
ls config/config.json
```

### Can't Access Dashboard

- Make sure bot is running (Terminal shows activity)
- Try http://127.0.0.1:8080 instead
- Check if port 8080 is blocked by firewall

### No Trades Happening

This is normal! The bot only trades when conditions match. In dry-run mode:
- Market must meet strategy criteria
- Might take hours or days for a signal
- Check logs: `tail -f user_data/logs/freqtrade.log`

---

## Safety Reminders

1. **Always start with dry-run** - Practice before using real money
2. **Run for at least 1-2 weeks** in dry-run before considering live
3. **Never invest more than you can afford to lose**
4. **When ready for live trading:**
   - Get API keys from your exchange
   - Only enable "Trade" permission (NOT withdrawal!)
   - Start with small amounts

---

## Quick Reference Card

```
┌─────────────────────────────────────────────────┐
│           MAC QUICK REFERENCE                    │
├─────────────────────────────────────────────────┤
│                                                  │
│  FIRST TIME:                                     │
│    cd /path/to/freqtrade.io                     │
│    ./setup.sh                                    │
│                                                  │
│  START BOT (PRACTICE):                           │
│    ./start_bot.sh                                │
│                                                  │
│  STOP BOT:                                       │
│    Ctrl + C                                      │
│                                                  │
│  VIEW DASHBOARD:                                 │
│    http://localhost:8080                         │
│    User: freqtrader / Pass: freqtrader          │
│                                                  │
│  VIEW LOGS:                                      │
│    tail -f user_data/logs/freqtrade.log         │
│                                                  │
│  NEED HELP:                                      │
│    See docs/GETTING_STARTED.md                   │
│    See docs/FAQ.md                               │
│                                                  │
└─────────────────────────────────────────────────┘
```

---

## Next Steps

1. **Run dry-run for 1-2 weeks** - Get comfortable with the system
2. **Study the dashboard** - Understand what each metric means
3. **Read the strategy docs** - Learn how each strategy works (`docs/STRATEGIES.md`)
4. **Try backtesting** - See how strategies performed historically
5. **Join the community** - Get help and share experiences

Happy practicing!
