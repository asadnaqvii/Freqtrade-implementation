"""
Bollinger Bands Breakout Strategy
Risk Level: Medium
Best For: Volatile markets

This strategy uses Bollinger Bands to identify volatility breakouts.
When price touches the lower band and bounces, it's a potential buy signal.

Strategy:
- Buy when price touches lower Bollinger Band and RSI is oversold
- Sell when price reaches upper Bollinger Band or middle band
- Uses volume confirmation
- Good for capturing volatility moves
"""

from freqtrade.strategy import IStrategy, DecimalParameter, IntParameter
from pandas import DataFrame
import talib.abstract as ta


class BollingerBreakout(IStrategy):
    """
    Bollinger Bands breakout strategy
    """

    # Strategy metadata
    INTERFACE_VERSION = 3

    # Minimal ROI - Take profit targets
    minimal_roi = {
        "0": 0.25,   # 25% profit target
        "30": 0.18,  # 18% after 30 minutes
        "80": 0.12,  # 12% after 80 minutes
        "150": 0.08  # 8% after 2.5 hours
    }

    # Stop loss
    stoploss = -0.08  # 8% stop loss

    # Trailing stop
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.025
    trailing_only_offset_is_reached = True

    # Timeframe
    timeframe = '5m'

    # Run "populate_indicators()" only for new candle
    process_only_new_candles = True

    # Experimental settings
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # Number of candles the strategy requires
    startup_candle_count: int = 200

    # Strategy parameters
    bb_period = IntParameter(15, 30, default=20, space="buy")
    bb_std = DecimalParameter(1.5, 3.0, default=2.0, space="buy")
    rsi_buy = IntParameter(20, 40, default=35, space="buy")
    rsi_sell = IntParameter(60, 80, default=65, space="sell")

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Add indicators to the dataframe
        """
        # Bollinger Bands
        bollinger = ta.BBANDS(
            dataframe,
            timeperiod=self.bb_period.value,
            nbdevup=self.bb_std.value,
            nbdevdn=self.bb_std.value
        )
        dataframe['bb_lowerband'] = bollinger['lowerband']
        dataframe['bb_middleband'] = bollinger['middleband']
        dataframe['bb_upperband'] = bollinger['upperband']

        # Calculate Bollinger Band width (for volatility)
        dataframe['bb_width'] = (
            (dataframe['bb_upperband'] - dataframe['bb_lowerband']) /
            dataframe['bb_middleband']
        )

        # RSI
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # EMA for trend
        dataframe['ema_50'] = ta.EMA(dataframe, timeperiod=50)
        dataframe['ema_200'] = ta.EMA(dataframe, timeperiod=200)

        # Volume
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=20).mean()

        # ATR for volatility measurement
        dataframe['atr'] = ta.ATR(dataframe, timeperiod=14)

        # Stochastic for additional confirmation
        stoch = ta.STOCH(dataframe)
        dataframe['slowk'] = stoch['slowk']
        dataframe['slowd'] = stoch['slowd']

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Entry signal logic:
        - Price touches or goes below lower Bollinger Band
        - RSI is oversold
        - Price is above 200 EMA (uptrend)
        - Volume is above average (confirmation)
        - Stochastic is oversold
        """
        dataframe.loc[
            (
                # Price at or below lower band
                (dataframe['close'] <= dataframe['bb_lowerband'] * 1.01) &

                # RSI oversold
                (dataframe['rsi'] < self.rsi_buy.value) &

                # Long-term uptrend
                (dataframe['close'] > dataframe['ema_200']) &

                # Volume confirmation
                (dataframe['volume'] > dataframe['volume_ma']) &

                # Stochastic oversold
                (dataframe['slowk'] < 20) &

                # BB width is not too tight (enough volatility)
                (dataframe['bb_width'] > 0.01) &

                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        """
        Exit signal logic:
        - Price reaches upper Bollinger Band
        - Or RSI is overbought
        - Or price crosses below middle band after being above
        """
        dataframe.loc[
            (
                # Price at upper band
                (dataframe['close'] >= dataframe['bb_upperband'] * 0.99) |

                # RSI overbought
                (dataframe['rsi'] > self.rsi_sell.value) |

                # Price crosses below middle band
                (
                    (dataframe['close'] < dataframe['bb_middleband']) &
                    (dataframe['close'].shift(1) >= dataframe['bb_middleband'].shift(1))
                )
            ),
            'exit_long'] = 1

        return dataframe
