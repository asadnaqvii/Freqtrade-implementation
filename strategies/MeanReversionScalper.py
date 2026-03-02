"""
5-Minute Mean Reversion Scalping Strategy (Bollinger + RSI) (Manit's strategy)
Risk Level: Medium
Best For: High-liquidity pairs in ranging markets

Captures short-term oversold bounces using Bollinger Bands and RSI.
Tight take-profit (+0.5%) and stop-loss (-0.4%) for quick feedback.

Expected: 5-15 trades per day per pair, 20-40 daily across BTC/ETH/SOL.
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class MeanReversionScalper(IStrategy):

    INTERFACE_VERSION = 3

    # Take profit: +0.5% as quickly as possible
    minimal_roi = {
        "0": 0.005,
    }

    # Stop loss: -0.4%
    stoploss = -0.004

    # No trailing stop — fixed TP/SL for clean scalping
    trailing_stop = False

    # 5-minute candles
    timeframe = '5m'

    process_only_new_candles = True
    use_exit_signal = False  # Rely on ROI and stoploss only (Option A)
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # BB(20) needs ~25 candles to warm up, RSI(14) needs ~20
    startup_candle_count: int = 30

    # Allow enough concurrent trades for 3 pairs generating signals
    max_open_trades = 6

    # Enable shorting (for futures mode)
    can_short = True

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Bollinger Bands (20, 2 std dev)
        bollinger = ta.BBANDS(dataframe, timeperiod=20, nbdevup=2.0, nbdevdn=2.0)
        dataframe['bb_lower'] = bollinger['lowerband']
        dataframe['bb_upper'] = bollinger['upperband']

        # RSI (14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # Volume moving average (20-period)
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Long entry: oversold bounce
        dataframe.loc[
            (
                (dataframe['close'] < dataframe['bb_lower']) &
                (dataframe['rsi'] < 30) &
                (dataframe['volume'] > dataframe['volume_ma']) &
                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        # Short entry: overbought reversal (futures only)
        dataframe.loc[
            (
                (dataframe['close'] > dataframe['bb_upper']) &
                (dataframe['rsi'] > 70) &
                (dataframe['volume'] > dataframe['volume_ma']) &
                (dataframe['volume'] > 0)
            ),
            'enter_short'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit handled by minimal_roi (+0.5%) and stoploss (-0.4%)
        return dataframe
