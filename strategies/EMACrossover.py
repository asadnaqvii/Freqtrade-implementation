"""
EMA Crossover Strategy
Risk Level: Low-Medium
Best For: Trending markets

This strategy uses Exponential Moving Average (EMA) crossovers to identify
trend changes and trading opportunities.

Strategy:
- Buy when fast EMA crosses above slow EMA (golden cross)
- Sell when fast EMA crosses below slow EMA (death cross)
- Includes RSI filter to avoid overbought entries
- Uses volume confirmation
"""

from freqtrade.strategy import IStrategy, IntParameter
from pandas import DataFrame
import talib.abstract as ta


class EMACrossover(IStrategy):
    """
    EMA Crossover strategy - follows market trends
    """

    # Strategy metadata
    INTERFACE_VERSION = 3

    # Minimal ROI - Take profit targets
    minimal_roi = {
        "0": 0.20,   # 20% profit target
        "40": 0.15,  # 15% after 40 minutes
        "100": 0.10, # 10% after 100 minutes
        "180": 0.05  # 5% after 3 hours
    }

    # Stop loss
    stoploss = -0.07  # 7% stop loss

    # Trailing stop
    trailing_stop = True
    trailing_stop_positive = 0.02
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    # Timeframe
    timeframe = '15m'

    # Run "populate_indicators()" only for new candle
    process_only_new_candles = True

    # Experimental settings
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires
    startup_candle_count: int = 200

    # Strategy parameters
    fast_ema = IntParameter(5, 20, default=12, space="buy")
    slow_ema = IntParameter(20, 100, default=26, space="buy")
    rsi_threshold = IntParameter(50, 80, default=70, space="buy")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Add indicators to the dataframe
        """
        # Fast and Slow EMAs
        dataframe['ema_fast'] = ta.EMA(dataframe, timeperiod=self.fast_ema.value)
        dataframe['ema_slow'] = ta.EMA(dataframe, timeperiod=self.slow_ema.value)

        # Longer-term EMA for trend confirmation
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # MACD for additional confirmation
        macd = ta.MACD(dataframe)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']
        dataframe['macdhist'] = macd['macdhist']

        # Volume
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=20).mean()

        # ATR for volatility
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry signal logic:
        - Fast EMA crosses above Slow EMA (golden cross)
        - Price is above 200 EMA (long-term uptrend)
        - RSI is not overbought (< 70)
        - Volume is above average
        - MACD is positive
        """
        dataframe.loc[
            (
                # EMA crossover
                (dataframe['ema_fast'] > dataframe['ema_slow']) &
                (dataframe['ema_fast'].shift(1) <= dataframe['ema_slow'].shift(1)) &

                # Trend confirmation
                (dataframe['close'] > dataframe['ema_200']) &

                # Not overbought
                (dataframe['rsi'] < self.rsi_threshold.value) &

                # Volume confirmation
                (dataframe['volume'] > dataframe['volume_ma']) &

                # MACD confirmation
                (dataframe['macd'] > dataframe['macdsignal']) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit signal logic:
        - Fast EMA crosses below Slow EMA (death cross)
        - Or MACD turns negative
        """
        dataframe.loc[
            (
                # EMA crossover (bearish)
                (
                    (dataframe['ema_fast'] < dataframe['ema_slow']) &
                    (dataframe['ema_fast'].shift(1) >= dataframe['ema_slow'].shift(1))
                ) |
                # MACD turns negative
                (
                    (dataframe['macd'] < dataframe['macdsignal']) &
                    (dataframe['macd'].shift(1) >= dataframe['macdsignal'].shift(1))
                )
            ),
            'exit_long'] = 1

        return dataframe
