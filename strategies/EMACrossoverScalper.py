"""
EMA Crossover Scalping Strategy on 5m timeframe (Manit's strategy #2)
Risk Level: Medium
Best For: BTC/USDT + ETH/USDT simultaneously

Entry: EMA(9) crosses above EMA(21) with RSI(14) between 45-65
and volume above 20-period volume MA.

Exit: Take profit 0.6%, Stop loss 0.5%, Trailing stop activates at 0.4%
and trails by 0.2%.

Expected: 30-80+ trades/month across both pairs, ~3% monthly net.
"""

from freqtrade.strategy import IStrategy
from pandas import DataFrame
import talib.abstract as ta


class EMACrossoverScalper(IStrategy):

    INTERFACE_VERSION = 3

    # Take profit: 0.6%
    minimal_roi = {
        "0": 0.006,
    }

    # Stop loss: 0.5%
    stoploss = -0.005

    # Trailing stop: activate at 0.4% profit, trail by 0.2%
    trailing_stop = True
    trailing_stop_positive = 0.002       # trail by 0.2%
    trailing_stop_positive_offset = 0.004  # activate at 0.4% profit
    trailing_only_offset_is_reached = True

    # 5-minute candles
    timeframe = '5m'

    process_only_new_candles = True
    use_exit_signal = False   # rely on ROI, stoploss, and trailing stop
    exit_profit_only = False
    ignore_roi_if_entry_signal = False

    # EMA(21) needs ~25 candles, volume MA(20) needs ~25
    startup_candle_count: int = 30

    # Max 3 open trades at a time
    max_open_trades = 3

    # Spot trading only
    can_short = False

    def populate_indicators(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # EMA(9) — fast
        dataframe['ema9'] = ta.EMA(dataframe, timeperiod=9)

        # EMA(21) — slow
        dataframe['ema21'] = ta.EMA(dataframe, timeperiod=21)

        # RSI(14)
        dataframe['rsi'] = ta.RSI(dataframe, timeperiod=14)

        # Volume moving average (20-period)
        dataframe['volume_ma'] = dataframe['volume'].rolling(window=20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Long entry: EMA(9) crosses above EMA(21)
        # + RSI between 45-65 (filters overbought and weak signals)
        # + Volume above 20-period MA (confirms real momentum)
        dataframe.loc[
            (
                (dataframe['ema9'] > dataframe['ema21']) &
                (dataframe['ema9'].shift(1) <= dataframe['ema21'].shift(1)) &
                (dataframe['rsi'] >= 45) &
                (dataframe['rsi'] <= 65) &
                (dataframe['volume'] > dataframe['volume_ma']) &
                (dataframe['volume'] > 0)
            ),
            'enter_long'] = 1

        return dataframe

    def populate_exit_trend(self, dataframe: DataFrame, metadata: dict) -> DataFrame:
        # Exit handled by minimal_roi (0.6%), stoploss (0.5%), and trailing stop
        return dataframe
