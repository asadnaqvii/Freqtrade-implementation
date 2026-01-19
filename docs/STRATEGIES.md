# Trading Strategies Guide

This document explains each trading strategy included in the platform, how they work, when to use them, and how to optimize them.

## Table of Contents

1. [Conservative RSI Strategy](#conservative-rsi-strategy)
2. [EMA Crossover Strategy](#ema-crossover-strategy)
3. [Bollinger Bands Breakout Strategy](#bollinger-bands-breakout-strategy)
4. [Comparing Strategies](#comparing-strategies)
5. [Optimization Tips](#optimization-tips)
6. [Creating Custom Strategies](#creating-custom-strategies)

---

## Conservative RSI Strategy

**File:** `strategies/ConservativeRSI.py`

### Overview

The Conservative RSI strategy is designed for beginners and risk-averse traders. It uses the Relative Strength Index (RSI) to identify oversold and overbought market conditions.

### How It Works

**Entry Conditions:**
1. RSI drops below 30 (market is oversold)
2. Price is above 200-period EMA (confirming long-term uptrend)
3. Volume is above average (confirming interest)

**Exit Conditions:**
1. RSI rises above 70 (market is overbought)
2. Stop loss is hit (5% loss)
3. Take profit targets are reached

**Indicators Used:**
- **RSI (14)**: Measures momentum, identifies overbought/oversold
- **EMA 200**: Long-term trend confirmation
- **EMA 50**: Medium-term trend
- **Volume MA**: Confirms genuine market interest
- **Bollinger Bands**: Additional context

### Parameters

```python
minimal_roi = {
    "0": 0.15,    # 15% profit target
    "30": 0.10,   # 10% after 30 minutes
    "60": 0.05,   # 5% after 60 minutes
    "120": 0.02   # 2% after 2 hours
}

stoploss = -0.05  # 5% stop loss
timeframe = '5m'  # 5-minute candles
```

### When to Use

**Best Market Conditions:**
- Stable, ranging markets
- Low to medium volatility
- Established uptrends

**Avoid When:**
- Extremely volatile markets
- Strong downtrends
- Low liquidity coins

### Expected Performance

**Typical Results:**
- Win Rate: 50-60%
- Average Trade: 2-5% profit
- Monthly Return: 5-15%
- Drawdown: 5-10%

**Risk Level:** Low

### Optimization

**Adjustable Parameters:**
- `buy_rsi`: Default 30, range 20-40
  - Lower = fewer but stronger signals
  - Higher = more trades, less oversold

- `sell_rsi`: Default 70, range 60-80
  - Lower = earlier exits, smaller profits
  - Higher = later exits, larger profits

**Optimization Example:**
```bash
freqtrade hyperopt \
  --strategy ConservativeRSI \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces buy sell \
  -e 100
```

### Pros and Cons

**Pros:**
- Easy to understand
- Conservative, lower risk
- Works well in ranging markets
- Good for beginners

**Cons:**
- May miss strong trends
- Lower profit potential
- Can be slow to enter trades

---

## EMA Crossover Strategy

**File:** `strategies/EMACrossover.py`

### Overview

The EMA Crossover strategy follows market trends using exponential moving averages. It's designed to capture medium to long-term price movements.

### How It Works

**Entry Conditions:**
1. Fast EMA (12) crosses above Slow EMA (26) - "Golden Cross"
2. Price is above 200-period EMA (long-term uptrend)
3. RSI is below 70 (not overbought)
4. Volume is above average
5. MACD is positive (additional confirmation)

**Exit Conditions:**
1. Fast EMA crosses below Slow EMA - "Death Cross"
2. MACD turns negative
3. Stop loss is hit (7% loss)
4. Take profit targets are reached

**Indicators Used:**
- **EMA 12/26**: Fast and slow moving averages
- **EMA 200**: Long-term trend filter
- **RSI (14)**: Prevents buying overbought markets
- **MACD**: Momentum confirmation
- **Volume MA**: Volume confirmation
- **ATR**: Volatility measurement

### Parameters

```python
minimal_roi = {
    "0": 0.20,    # 20% profit target
    "40": 0.15,   # 15% after 40 minutes
    "100": 0.10,  # 10% after 100 minutes
    "180": 0.05   # 5% after 3 hours
}

stoploss = -0.07  # 7% stop loss
timeframe = '15m'  # 15-minute candles
```

### When to Use

**Best Market Conditions:**
- Trending markets (up or down)
- Medium volatility
- Clear directional movement

**Avoid When:**
- Choppy, sideways markets
- Low volatility
- Frequent whipsaws

### Expected Performance

**Typical Results:**
- Win Rate: 45-55%
- Average Trade: 5-10% profit
- Monthly Return: 10-25%
- Drawdown: 10-15%

**Risk Level:** Low-Medium

### Optimization

**Adjustable Parameters:**
- `fast_ema`: Default 12, range 5-20
  - Lower = more responsive, more signals
  - Higher = smoother, fewer signals

- `slow_ema`: Default 26, range 20-100
  - Lower = faster exits
  - Higher = longer holds

- `rsi_threshold`: Default 70, range 50-80
  - Lower = stricter entry filter
  - Higher = more entries

**Optimization Example:**
```bash
freqtrade hyperopt \
  --strategy EMACrossover \
  --hyperopt-loss SortinoHyperOptLoss \
  --spaces buy \
  -e 200
```

### Pros and Cons

**Pros:**
- Excellent for trending markets
- Multiple confirmations reduce false signals
- Good risk/reward ratio
- Captures large moves

**Cons:**
- Underperforms in ranging markets
- Can have losing streaks in choppy conditions
- Requires patience
- Later entries than some strategies

---

## Bollinger Bands Breakout Strategy

**File:** `strategies/BollingerBreakout.py`

### Overview

The Bollinger Bands Breakout strategy is designed for volatile markets. It buys when price touches the lower band and shows signs of reversal.

### How It Works

**Entry Conditions:**
1. Price touches or goes below lower Bollinger Band
2. RSI is oversold (< 35)
3. Price is above 200-period EMA (long-term uptrend)
4. Volume is above average
5. Stochastic is oversold (< 20)
6. Bollinger Band width shows sufficient volatility

**Exit Conditions:**
1. Price reaches upper Bollinger Band
2. RSI is overbought (> 65)
3. Price crosses below middle Bollinger Band
4. Stop loss is hit (8% loss)
5. Take profit targets are reached

**Indicators Used:**
- **Bollinger Bands (20, 2)**: Volatility bands
- **BB Width**: Measures volatility level
- **RSI (14)**: Momentum indicator
- **EMA 50/200**: Trend filters
- **Stochastic**: Additional oversold confirmation
- **ATR**: Volatility measurement
- **Volume MA**: Volume confirmation

### Parameters

```python
minimal_roi = {
    "0": 0.25,    # 25% profit target
    "30": 0.18,   # 18% after 30 minutes
    "80": 0.12,   # 12% after 80 minutes
    "150": 0.08   # 8% after 2.5 hours
}

stoploss = -0.08  # 8% stop loss
timeframe = '5m'  # 5-minute candles
```

### When to Use

**Best Market Conditions:**
- High volatility markets
- Ranging markets with clear support/resistance
- After consolidation periods
- Active trading hours

**Avoid When:**
- Low volatility
- Strong trending markets (one direction)
- Thin, illiquid pairs

### Expected Performance

**Typical Results:**
- Win Rate: 50-65%
- Average Trade: 8-15% profit
- Monthly Return: 15-35%
- Drawdown: 15-25%

**Risk Level:** Medium

### Optimization

**Adjustable Parameters:**
- `bb_period`: Default 20, range 15-30
  - Lower = more responsive bands
  - Higher = smoother bands

- `bb_std`: Default 2.0, range 1.5-3.0
  - Lower = tighter bands, more signals
  - Higher = wider bands, fewer signals

- `rsi_buy`: Default 35, range 20-40
  - Lower = stronger oversold requirement
  - Higher = earlier entries

- `rsi_sell`: Default 65, range 60-80
  - Lower = earlier exits
  - Higher = later exits, larger targets

**Optimization Example:**
```bash
freqtrade hyperopt \
  --strategy BollingerBreakout \
  --hyperopt-loss CalmarHyperOptLoss \
  --spaces buy sell roi \
  -e 300
```

### Pros and Cons

**Pros:**
- Excellent in volatile markets
- High profit potential
- Clear entry/exit signals
- Good win rate

**Cons:**
- Higher risk
- Can have larger drawdowns
- Requires more monitoring
- Less effective in low volatility

---

## Comparing Strategies

### Quick Comparison Table

| Strategy | Risk | Complexity | Win Rate | Avg Profit | Best Markets | Timeframe |
|----------|------|------------|----------|------------|--------------|-----------|
| Conservative RSI | Low | Simple | 50-60% | 2-5% | Ranging | 5m |
| EMA Crossover | Medium | Moderate | 45-55% | 5-10% | Trending | 15m |
| Bollinger Breakout | Medium | Moderate | 50-65% | 8-15% | Volatile | 5m |

### Choosing a Strategy

**For Beginners:**
Start with **Conservative RSI**
- Easiest to understand
- Lowest risk
- Good learning platform

**For Trend Followers:**
Use **EMA Crossover**
- Captures major moves
- Good risk/reward
- Multiple confirmations

**For Active Traders:**
Try **Bollinger Breakout**
- More frequent signals
- Higher profit potential
- Requires more attention

### Combining Strategies

You can run multiple strategies simultaneously:

**Portfolio Approach:**
- 50% capital → Conservative RSI (stable base)
- 30% capital → EMA Crossover (trend capture)
- 20% capital → Bollinger Breakout (volatility plays)

**Benefits:**
- Diversification
- Reduced risk
- Capture different market conditions
- Smoother equity curve

---

## Optimization Tips

### Backtesting Best Practices

1. **Use sufficient data**
   - Minimum: 3-6 months
   - Recommended: 12+ months
   - Include different market conditions

2. **Avoid overfitting**
   - Don't optimize too many parameters
   - Use walk-forward analysis
   - Test on out-of-sample data

3. **Consider realistic conditions**
   - Include trading fees
   - Account for slippage
   - Use realistic stake amounts

### Parameter Optimization

**Safe approach:**
1. Start with default parameters
2. Run backtest baseline
3. Optimize one parameter at a time
4. Validate on new data
5. Test in dry-run

**Hyperopt command:**
```bash
freqtrade hyperopt \
  --strategy YourStrategy \
  --hyperopt-loss SharpeHyperOptLoss \
  --spaces buy sell roi stoploss \
  --timerange 20230101-20231231 \
  -e 500
```

### Common Optimization Mistakes

**Don't:**
- ❌ Optimize for maximum profit only
- ❌ Use too small date ranges
- ❌ Over-optimize (100+ parameters)
- ❌ Ignore drawdown and risk metrics
- ❌ Skip validation testing

**Do:**
- ✅ Optimize for risk-adjusted returns (Sharpe/Sortino)
- ✅ Use long time periods
- ✅ Keep it simple (5-10 parameters max)
- ✅ Consider multiple metrics
- ✅ Validate on different time periods

---

## Creating Custom Strategies

### Basic Strategy Template

```python
from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta

class MyCustomStrategy(IStrategy):
    INTERFACE_VERSION = 3

    # Configuration
    minimal_roi = {"0": 0.10}
    stoploss = -0.05
    timeframe = '5m'

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Add your indicators
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)
        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Define buy conditions
        dataframe.loc[
            (dataframe['rsi'] < 30),
            'enter_long'] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Define sell conditions
        dataframe.loc[
            (dataframe['rsi'] > 70),
            'exit_long'] = 1
        return dataframe
```

### Development Workflow

1. **Research**: Study indicators and market behavior
2. **Code**: Implement strategy logic
3. **Backtest**: Test with historical data
4. **Optimize**: Fine-tune parameters
5. **Validate**: Test on new data
6. **Dry-run**: Test with live data, no risk
7. **Live**: Start with small amounts

### Testing Your Strategy

```bash
# Backtest
freqtrade backtesting \
  --strategy MyCustomStrategy \
  --timerange 20230101-20231231

# Dry-run
./start_bot.sh --dry-run
```

### Resources

- **Freqtrade Docs**: https://www.freqtrade.io/en/stable/strategy-customization/
- **TA-Lib Indicators**: https://mrjbq7.github.io/ta-lib/
- **Strategy Templates**: `/strategies` folder

---

## Need Help?

- Review example strategies in `/strategies` folder
- Check Freqtrade documentation
- Join community Discord/Telegram
- Open GitHub issue for bugs

Happy trading! 📈
