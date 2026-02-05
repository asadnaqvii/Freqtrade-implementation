"""
Conservative RSI Strategy
Risk Level: Low
Best For: Beginners, stable markets

This strategy uses the Relative Strength Index (RSI) to identify oversold
and overbought conditions. It's conservative and includes strong risk management.

Strategy:
- Buy when RSI < 30 (oversold) and price is above 200 EMA (uptrend)
- Sell when RSI > 70 (overbought) or stop loss is hit
- Uses 200 EMA to confirm overall trend
- Conservative position sizing and stop loss
"""

from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from pandas import DataFrame
import talib.abstract as ta


class ConservativeRSI(IStrategy):
    """
    Conservative RSI-based strategy for beginners
    """

    # Strategy metadata
    INTERFACE_VERSION = 3

    # Minimal ROI - Take profit targets
    minimal_roi = {
        "0": 0.15,   # 15% profit target
        "30": 0.10,  # 10% after 30 minutes
        "60": 0.05,  # 5% after 60 minutes
        "120": 0.02  # 2% after 2 hours
    }

    # Stop loss
    stoploss = -0.05  # 5% stop loss (conservative)

    # Trailing stop loss (optional, can be enabled)
    trailing_stop = True
    trailing_stop_positive = 0.01  # Start trailing after 1% profit
    trailing_stop_positive_offset = 0.02  # Trigger at 2% profit
    trailing_only_offset_is_reached = True

    # Timeframe
    timeframe = '5m'

    # Run "populate_indicators()" only for new candle
    process_only_new_candles = True

    # Experimental settings (configuration will overwrite these if set)
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires before producing valid signals
    startup_candle_count: int = 200

    # Strategy parameters (can be optimized)
    buy_rsi = IntParameter(20, 40, default=30, space="buy")
    sell_rsi = IntParameter(60, 80, default=70, space="sell")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Add indicators to the dataframe
        """
        # RSI (Relative Strength Index)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # EMA (Exponential Moving Average) for trend
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)

        # Volume indicator
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=20).mean()

        # Bollinger Bands for additional context
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_upperband'] = bollinger['upperband']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry signal logic:
        - RSI is oversold (< 30)
        - Price is above 200 EMA (long-term uptrend)
        - Volume is above average (confirmation)
        """
        dataframe.loc[
            (
                (dataframe['rsi'] < self.buy_rsi.value) &  # Oversold
                (dataframe['close'] > dataframe['ema_200']) &  # Above long-term trend
                (dataframe['volume'] > dataframe['volume_ma']) &  # Volume confirmation
                (dataframe['volume'] > 0)  # Make sure volume is not zero
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit signal logic:
        - RSI is overbought (> 70)
        - Or stop loss is hit (handled by stoploss parameter)
        """
        dataframe.loc[
            (
                (dataframe['rsi'] > self.sell_rsi.value)  # Overbought
            ),
            'exit_long'] = 1

        return dataframe
