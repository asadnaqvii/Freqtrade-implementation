# TrendPullbackStrategy.py
#
# Rebuilt from an audit of ~5.5 months of live Aura trade logs (EMA Crossover /
# Multi-Timeframe Momentum on BTC/ETH/ADA/SOL). Findings that drove every
# design decision below are in the accompanying explanation, not repeated
# here as comments — read that alongside this file.
#
# This is a starting point for backtesting and dry-run, NOT a finished,
# validated system. Do not deploy with real capital until you've run it
# through `freqtrade backtesting` and a multi-week dry-run and are satisfied
# with the drawdown, not just the total return.

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade
import talib.abstract as ta
import pandas as pd
from datetime import datetime


class TrendPullbackStrategy(IStrategy):
    """
    Long-only, trend-following pullback strategy on a 4h timeframe.

    Why 4h / why trend-following / why these ROI numbers is explained in
    chat, not here — this file is deliberately light on prose comments
    to avoid the strategy drifting out of sync with an explanation
    embedded in code.
    """

    INTERFACE_VERSION = 3
    timeframe = "4h"

    # Fee-aware ROI curve. Round-trip cost on your account has been running
    # ~0.2%. Every ROI tier here clears that by a wide margin so a "win"
    # is still a win after costs, not a coin flip against the spread.
    minimal_roi = {
        "0": 0.08,     # let a strong trend run
        "360": 0.05,   # after 15 candles (60h), take 5%+
        "1440": 0.025, # after 60 candles (10 days), take 2.5%+
        "4320": 0.01,  # after 30 days, take anything above 1%
    }

    # Stoploss is a hard backstop; the real exit logic is the ATR-based
    # trailing stop below. This just caps worst-case loss per trade.
    stoploss = -0.06

    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    can_short = False  # spot account — long only

    startup_candle_count = 220

    # Tunable via hyperopt
    ema_fast = IntParameter(20, 60, default=50, space="buy")
    ema_slow = IntParameter(150, 250, default=200, space="buy")
    rsi_buy_threshold = IntParameter(35, 50, default=45, space="buy")
    atr_stop_mult = DecimalParameter(1.5, 4.0, default=2.5, space="sell")

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.ema_fast.value)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.ema_slow.value)
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)

        # volatility filter: median-normalized ATR, avoid dead / illiquid ranges
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        dataframe["atr_pct_median"] = dataframe["atr_pct"].rolling(100).median()

        # volume confirmation
        dataframe["vol_mean20"] = dataframe["volume"].rolling(20).mean()

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (
                # established uptrend, not chasing chop
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                # price pulled back toward the fast EMA (buying the dip in
                # the trend, not the breakout everyone else is buying)
                & (dataframe["close"] <= dataframe["ema20"] * 1.01)
                & (dataframe["close"] >= dataframe["ema20"] * 0.97)
                # momentum resetting up from a pullback, not oversold-and-falling
                & (dataframe["rsi"] > self.rsi_buy_threshold.value)
                & (dataframe["rsi"] < 65)
                # enough volatility to clear fees within a reasonable hold,
                # but not so much it's a coin-flip candle
                & (dataframe["atr_pct"] > dataframe["atr_pct_median"] * 0.7)
                # real participation, not a thin-book candle
                & (dataframe["volume"] > dataframe["vol_mean20"] * 0.8)
                & (dataframe["volume"] > 0)
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (
                # trend structure breaks
                (dataframe["ema_fast"] < dataframe["ema_slow"])
                | (dataframe["rsi"] > 78)  # overheated
            ),
            "exit_long",
        ] = 1
        return dataframe

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                        current_rate: float, current_profit: float, **kwargs) -> float:
        # ATR-based stop: let volatile pairs breathe, keep quiet pairs tight,
        # instead of one fixed percentage for every token in the whitelist
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss
        last_atr_pct = dataframe["atr_pct"].iloc[-1]
        dynamic_stop = -(last_atr_pct * self.atr_stop_mult.value)
        return max(dynamic_stop, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                            proposed_stake: float, min_stake, max_stake: float,
                            leverage: float, entry_tag, side: str, **kwargs) -> float:
        # Risk-based sizing instead of a flat $10/$100/whatever per trade:
        # size each position so a stop-out costs roughly the same fraction
        # of the account regardless of how volatile that particular pair is.
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return proposed_stake
        last_atr_pct = dataframe["atr_pct"].iloc[-1]
        stop_distance = max(last_atr_pct * self.atr_stop_mult.value, 0.01)

        total_capital = self.wallets.get_total_stake_amount()
        risk_per_trade = 0.01  # risk 1% of total capital per trade
        target_stake = (total_capital * risk_per_trade) / stop_distance

        return max(min(target_stake, max_stake), min_stake or 0)
