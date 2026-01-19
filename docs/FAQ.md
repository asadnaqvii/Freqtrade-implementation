# Frequently Asked Questions (FAQ)

## General Questions

### What is this platform?

This is an automated cryptocurrency trading platform built on Freqtrade. It executes trades based on predefined strategies without requiring constant manual intervention.

### Is this profitable?

There are **no guarantees** in trading. While backtesting may show positive results, past performance doesn't guarantee future profits. Only invest what you can afford to lose.

### How much money do I need to start?

- **Dry-run (practice)**: Free, uses virtual money
- **Live trading**: Minimum $100, recommended $500-1000
- Start small and scale up as you gain confidence

### Is this suitable for beginners?

Yes! The platform is designed to be beginner-friendly with:
- Easy setup wizard
- Pre-built conservative strategies
- Dry-run mode for practice
- Comprehensive documentation

However, you should still learn basic trading concepts and understand the risks.

## Setup & Installation

### What operating systems are supported?

- Linux (Ubuntu, Debian, etc.) - Recommended
- macOS
- Windows (via WSL or Docker)

### Installation failed. What should I do?

1. Check Python version: `python3 --version` (need 3.8+)
2. Try with sudo: `sudo ./setup.sh`
3. Install build tools:
   ```bash
   # Ubuntu/Debian
   sudo apt-get install build-essential python3-dev

   # macOS
   xcode-select --install
   ```
4. Check the logs for specific error messages

### How do I update the platform?

```bash
source venv/bin/activate
pip install --upgrade freqtrade
```

## Configuration

### Can I change settings after initial setup?

Yes! Edit `config/config.json` or run the wizard again:
```bash
python config_wizard.py
```

### Which exchange should I use?

**Binance** is recommended for beginners because:
- Low fees
- High liquidity
- Well-supported by Freqtrade
- Easy to use

Other good options: Coinbase Pro, Kraken, Bitfinex

### How do I get API keys?

**For Binance:**
1. Log into Binance.com
2. Go to Account → API Management
3. Create new API key
4. Enable "Spot & Margin Trading"
5. **DO NOT enable withdrawals** (security)
6. Copy key and secret

**For other exchanges**, the process is similar.

### Are my API keys safe?

Your API keys:
- Are stored locally in `config/config.json`
- Are never shared with anyone
- Should have withdrawals disabled
- Can be regenerated anytime if compromised

**Never commit config files with real API keys to Git!**

## Trading Strategies

### Which strategy should I use?

**For beginners**: Start with **ConservativeRSI**
- Lowest risk
- Easier to understand
- Good for learning

**For trend followers**: Use **EMACrossover**
- Medium risk
- Works well in trending markets
- Good win rate

**For volatile markets**: Try **BollingerBreakout**
- Higher risk, higher potential reward
- Best when markets are volatile
- Requires more monitoring

### Can I modify strategies?

Yes! Strategies are Python files in the `strategies/` folder. You can:
- Modify existing strategies
- Create custom strategies
- Adjust parameters
- Combine multiple indicators

See Freqtrade documentation for strategy development.

### Can I run multiple strategies at once?

Yes, but you'll need to:
1. Run multiple bot instances
2. Use different config files
3. Ensure sufficient capital for each

### How often does the bot trade?

This depends on:
- Market conditions
- Strategy parameters
- Timeframe (5m, 15m, 1h, etc.)
- Number of trading pairs

Typically: 1-10 trades per day per pair

## Dry-Run vs Live Trading

### What is dry-run mode?

Dry-run simulates trading with virtual money:
- Uses real market data
- Executes "paper trades"
- No real money at risk
- Perfect for testing

### How long should I run dry-run?

**Minimum 1-2 weeks** to:
- See how strategy performs
- Test different market conditions
- Verify bot works correctly
- Build confidence

Longer is better (1-3 months ideal).

### Can I switch from dry-run to live?

Yes! Just:
1. Set `"dry_run": false` in config
2. Add valid API keys
3. Restart the bot

### Should I paper trade before going live?

**Absolutely!** Always test in dry-run first. It's the #1 rule of automated trading.

## Risk Management

### What is a good stake amount?

**Conservative approach:**
- Risk 1-2% of capital per trade
- Example: $1000 capital = $10-20 per trade

**Aggressive approach:**
- Risk up to 5% per trade
- Higher potential returns, higher risk

### How many trades should I have open?

**Beginners**: 2-3 trades
**Intermediate**: 3-5 trades
**Advanced**: 5-10 trades

More trades = more diversification, but requires more capital.

### What stop loss should I use?

**Conservative**: 3-5%
**Moderate**: 5-8%
**Aggressive**: 8-12%

Lower stop loss = less risk but more frequent stops.

### How do I protect against big losses?

1. **Use stop losses** (always!)
2. **Set max daily loss** limits
3. **Start small** and scale gradually
4. **Diversify** across pairs
5. **Monitor regularly**
6. **Don't over-leverage**

## Monitoring & Maintenance

### How often should I check the bot?

**Live trading:**
- First week: Multiple times daily
- After that: Once or twice daily
- Weekly performance review

**Dry-run:**
- Every few days is fine

### What should I monitor?

- Open trades
- Profit/loss
- Win rate
- Strategy performance
- Market conditions
- Error logs

### The bot made a losing trade. Should I panic?

**No!** Losing trades are normal:
- No strategy wins 100% of the time
- Even 50-60% win rate can be profitable
- Risk management is key

**When to worry:**
- Multiple consecutive losses
- Losses exceed expected range
- Strategy not working as backtested

### Can I run the bot 24/7?

Yes! Crypto markets never close. Options:
- Run on your computer (must stay on)
- Run on cloud server (VPS) - Recommended
- Use Docker container
- Set up systemd service (Linux)

## Technical Issues

### Bot keeps stopping

**Check:**
- Internet connection stable
- Sufficient system resources
- Log files for error messages
- Exchange API status

**Common fixes:**
- Increase timeout settings
- Check API rate limits
- Restart bot
- Update dependencies

### API errors "Invalid signature"

**Cause:** Incorrect API keys or time sync issue

**Fix:**
1. Verify API keys are correct
2. Check system time is accurate:
   ```bash
   sudo ntpdate -s time.nist.gov
   ```
3. Regenerate API keys if needed

### "Insufficient funds" error

**Causes:**
- Not enough balance on exchange
- Funds locked in open orders
- Minimum order size not met

**Fix:**
- Add more funds
- Close some positions
- Reduce stake amount

### Bot not making any trades

**Possible reasons:**
1. Market conditions don't meet entry criteria
2. Dry-run without downloading data
3. Strategy parameters too restrictive
4. Trading pairs have low volume

**Debug:**
```bash
tail -f user_data/logs/freqtrade.log
```

Look for "SIGNAL" messages to see why trades aren't opening.

## Performance

### What's a good return rate?

**Realistic expectations:**
- Conservative: 3-10% monthly
- Moderate: 10-20% monthly
- Aggressive: 20-40% monthly (higher risk)

**Warning signs:**
- Promises of 100%+ monthly returns
- "Too good to be true" backtest results

### My backtests show 200% profit. Will I get that?

**No.** Backtesting shows what *could have* happened:
- Market conditions change
- Slippage and fees impact real trading
- Optimization bias (overfitting)

Expect **30-50% of backtest performance** in live trading.

### How do I improve performance?

1. **Optimize strategy parameters** (but avoid overfitting)
2. **Test different timeframes**
3. **Adjust risk settings**
4. **Try different pairs**
5. **Combine multiple strategies**
6. **Keep learning** and adapting

## Advanced Topics

### Can I use this for short selling?

Yes, Freqtrade supports shorting, but:
- Requires margin trading
- Higher risk
- Not recommended for beginners

### Can I use leverage?

Yes, but **not recommended** unless you're experienced:
- Can amplify losses
- Higher liquidation risk
- Requires careful risk management

### How do I create custom strategies?

1. Copy existing strategy file
2. Modify indicators and logic
3. Backtest thoroughly
4. Test in dry-run
5. Document your changes

See `strategies/` folder for examples.

### Can I run this on Raspberry Pi?

Yes! The platform is lightweight enough for Raspberry Pi 3 or 4:
```bash
# Install dependencies
sudo apt-get install python3-dev
./setup.sh
```

### How do I deploy to a VPS?

1. Choose provider (DigitalOcean, AWS, etc.)
2. Set up Ubuntu server
3. Clone repository
4. Run setup script
5. Configure as systemd service
6. Set up monitoring

## Safety & Security

### Is my money safe?

Your funds stay on the exchange. The bot only:
- Reads market data
- Places buy/sell orders
- Cannot withdraw funds (if configured correctly)

**Security tips:**
- Never enable withdrawal on API keys
- Use 2FA on exchange account
- Keep API keys secret
- Monitor regularly

### What if the bot goes crazy?

**Safety measures:**
- Stop loss limits maximum loss per trade
- Max open trades limits exposure
- Can stop bot anytime (Ctrl+C)
- Can disable API keys on exchange

**Emergency shutdown:**
1. Press Ctrl+C to stop bot
2. Disable API keys on exchange
3. Manually close positions if needed

### Should I share my configuration?

**Never share:**
- API keys
- API secrets
- Telegram tokens
- Personal configuration files

**Safe to share:**
- Strategy files (without keys)
- General settings
- Performance results (anonymized)

## Getting Help

### Where can I get support?

1. Check this FAQ
2. Read [GETTING_STARTED.md](GETTING_STARTED.md)
3. Review Freqtrade docs: https://www.freqtrade.io
4. Check log files
5. Open GitHub issue
6. Join community Discord/Telegram

### How do I report a bug?

1. Check if already reported
2. Gather information:
   - Error message
   - Log files
   - Steps to reproduce
3. Open GitHub issue with details

### Can I contribute?

Yes! Contributions welcome:
- Report bugs
- Suggest features
- Improve documentation
- Share strategies (without sensitive data)
- Help other users

---

**Still have questions?** Open an issue on GitHub or check the Freqtrade community forums.
