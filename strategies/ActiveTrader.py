"""
Active Trader Strategy
Risk Level: Medium-High
Best For: Testing and demonstration

This strategy trades more frequently using looser entry conditions.
It's designed to generate trades quickly for testing/demo purposes.

WARNING: This strategy is aggressive and meant for dry-run testing.
Do not use with real money without extensive backtesting!
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class ActiveTrader(IStrategy):
    """
    Active trading strategy - generates frequent trades for testing
    """

    INTERFACE_VERSION = 3

    # Quick profit taking
    minimal_roi = {
        "0": 0.03,    # 3% profit target
        "15": 0.02,   # 2% after 15 minutes
        "30": 0.01,   # 1% after 30 minutes
        "60": 0.005   # 0.5% after 1 hour
    }

    # Stop loss
    stoploss = -0.03  # 3% stop loss

    # Trailing stop
    trailing_stop = True
    trailing_stop_positive = 0.005
    trailing_stop_positive_offset = 0.01
    trailing_only_offset_is_reached = True

    # Timeframe - 1 minute for active trading
    timeframe = '1m'

    # Process every candle
    process_only_new_candles = True

    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Fewer candles needed to start
    startup_candle_count: int = 50

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """Add indicators"""
        # RSI with shorter period for faster signals
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=7)

        # Fast EMAs
        dataframe['ema_8'] = ta.EMA(dataframe, timeperiod=8)
        dataframe['ema_21'] = ta.EMA(dataframe, timeperiod=21)

        # MACD for momentum
        macd = ta.MACD(dataframe, fastperiod=8, slowperiod=17, signalperiod=9)
        dataframe['macd'] = macd['macd']
        dataframe['macdsignal'] = macd['macdsignal']

        # Bollinger Bands
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_middle'] = bollinger['middleband']
        dataframe['bb_upper'] = bollinger['upperband']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry when ANY of these conditions are met:
        1. RSI below 40 (slightly oversold)
        2. Price near lower Bollinger band
        3. EMA crossover (fast above slow)
        4. MACD crosses above signal
        """
        dataframe.loc[
            (
                # Condition 1: RSI dip
                (dataframe['rsi'] < 40) &
                (dataframe['rsi'].shift(1) >= 40)
            ) |
            (
                # Condition 2: Near lower BB
                (dataframe['close'] <= dataframe['bb_lower'] * 1.01) &
                (dataframe['rsi'] < 50)
            ) |
            (
                # Condition 3: EMA crossover
                (dataframe['ema_8'] > dataframe['ema_21']) &
                (dataframe['ema_8'].shift(1) <= dataframe['ema_21'].shift(1))
            ) |
            (
                # Condition 4: MACD crossover
                (dataframe['macd'] > dataframe['macdsignal']) &
                (dataframe['macd'].shift(1) <= dataframe['macdsignal'].shift(1))
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit when:
        1. RSI above 60 (becoming overbought)
        2. Price near upper Bollinger band
        3. MACD crosses below signal
        """
        dataframe.loc[
            (
                (dataframe['rsi'] > 60)
            ) |
            (
                (dataframe['close'] >= dataframe['bb_upper'] * 0.99)
            ) |
            (
                (dataframe['macd'] < dataframe['macdsignal']) &
                (dataframe['macd'].shift(1) >= dataframe['macdsignal'].shift(1))
            ),
            'exit_long'] = 1

        return dataframe
