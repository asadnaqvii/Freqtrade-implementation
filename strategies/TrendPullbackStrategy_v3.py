# TrendPullbackStrategy_v3.py
#
# Builds on v2 (partial profit-taking + sharpened momentum-fade exit).
# New in v3, and why — full reasoning is in chat, this is the short version:
#
#   1. BTC regime filter — don't take new entries on ANY pair unless BTC
#      itself is in an uptrend. Individual pairs can look locally "trending"
#      while the broader market is turning; this is the fix for the
#      correlation problem (your 6 "diversified" positions aren't really
#      diversified if they're all just crypto-goes-up bets).
#   2. Structural-loser pair blacklist — ZEC, UNI, SHIB, PEPE, AAVE, and
#      BTC itself (as a TRADED pair, not as the regime signal) have been
#      net losers in your live data. This is evidence-based, not a
#      hunch — it's directly off your own performance-by-pair numbers.
#      Revisit this blacklist periodically as more data comes in; a pair
#      that's losing on 2-4 trades isn't proven bad forever, just bad
#      so far.
#   3. Risk-per-trade nudged up modestly (1% -> 1.3%) as ONE lever, at
#      the SAME time as capital moves 1462 -> 2000. Nothing else about
#      sizing logic changed, deliberately, so any shift in outcomes is
#      attributable.
#
# Still true from v2: this is a hypothesis given the data available, not
# a validated result. Backtest v3 against v2 and v1 before trusting it
# live, and give it real time before judging it — a handful of weeks is
# not enough data to confirm or deny any of these changes worked.

from freqtrade.strategy import IStrategy, IntParameter, DecimalParameter
from freqtrade.persistence import Trade
import talib.abstract as ta
import pandas as pd
from datetime import datetime


class TrendPullbackStrategy_v3(IStrategy):
    INTERFACE_VERSION = 3
    timeframe = "4h"

    minimal_roi = {
        "0": 0.10,
        "360": 0.06,
        "1440": 0.03,
        "4320": 0.01,
    }

    stoploss = -0.06
    trailing_stop = True
    trailing_stop_positive = 0.015
    trailing_stop_positive_offset = 0.03
    trailing_only_offset_is_reached = True

    position_adjustment_enable = True
    max_entry_position_adjustment = 1

    process_only_new_candles = True
    use_exit_signal = True
    exit_profit_only = False
    ignore_roi_if_entry_signal = False
    can_short = False

    startup_candle_count = 220

    # Circuit breakers, from config_v3.json. They live here rather than in the
    # config because freqtrade 2026.7 rejects a `protections` key outright --
    # "DEPRECATED: Setting 'protections' in the configuration is deprecated" is
    # a hard configuration error, not a warning, and the bot refuses to start.
    #
    # Nothing previously stopped this account re-entering a pair it had just
    # been stopped out of -- the pullback setup can still read as valid on the
    # very next candle -- or trading straight through a drawdown. These are the
    # only rules in the system that bound how bad a bad week gets.
    protections = [
        # A pair that just exited is not a fresh setup three candles later.
        {"method": "CooldownPeriod", "stop_duration_candles": 3},
        # Stop entirely after a 10% drawdown across the last 60 candles.
        {"method": "MaxDrawdown", "lookback_period_candles": 60, "trade_limit": 10,
         "stop_duration_candles": 12, "max_allowed_drawdown": 0.1},
        # Three stop-losses inside 24 candles says the regime is not the one
        # this strategy assumes. Pause instead of paying to keep finding out.
        {"method": "StoplossGuard", "lookback_period_candles": 24, "trade_limit": 3,
         "stop_duration_candles": 12, "only_per_pair": False},
    ]

    ema_fast = IntParameter(20, 60, default=50, space="buy")
    ema_slow = IntParameter(150, 250, default=200, space="buy")
    rsi_buy_threshold = IntParameter(35, 50, default=45, space="buy")
    atr_stop_mult = DecimalParameter(1.5, 4.0, default=2.5, space="sell")
    partial_profit_trigger = DecimalParameter(0.015, 0.04, default=0.025, space="sell")
    partial_exit_fraction = DecimalParameter(0.3, 0.6, default=0.5, space="sell")

    # Sizing: single lever moved up from v1/v2's 0.01, alongside the
    # capital increase to $2000 — see reasoning above.
    risk_per_trade = DecimalParameter(0.008, 0.02, default=0.013, space="sell")

    def informative_pairs(self):
        # Pulls BTC/USDT 4h data regardless of whether BTC is in the
        # tradeable whitelist — this is a regime signal, not a trade target.
        return [("BTC/USDT", self.timeframe)]

    def populate_indicators(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe["ema_fast"] = ta.EMA(dataframe, timeperiod=self.ema_fast.value)
        dataframe["ema_slow"] = ta.EMA(dataframe, timeperiod=self.ema_slow.value)
        dataframe["ema20"] = ta.EMA(dataframe, timeperiod=20)
        dataframe["rsi"] = ta.RSI(dataframe, timeperiod=14)
        dataframe["atr"] = ta.ATR(dataframe, timeperiod=14)
        dataframe["atr_pct"] = dataframe["atr"] / dataframe["close"]
        dataframe["atr_pct_median"] = dataframe["atr_pct"].rolling(100).median()
        dataframe["vol_mean20"] = dataframe["volume"].rolling(20).mean()

        dataframe["adx"] = ta.ADX(dataframe, timeperiod=14)
        dataframe["adx_falling"] = dataframe["adx"] < dataframe["adx"].shift(2)
        macd = ta.MACD(dataframe)
        dataframe["macd_hist"] = macd["macdhist"]
        dataframe["macd_hist_falling"] = dataframe["macd_hist"] < dataframe["macd_hist"].shift(1)

        # --- BTC market-regime filter ---
        if self.dp:
            btc = self.dp.get_pair_dataframe("BTC/USDT", self.timeframe)
            btc["btc_ema_fast"] = ta.EMA(btc, timeperiod=self.ema_fast.value)
            btc["btc_ema_slow"] = ta.EMA(btc, timeperiod=self.ema_slow.value)
            btc["btc_uptrend"] = btc["btc_ema_fast"] > btc["btc_ema_slow"]
            btc_slim = btc[["date", "btc_uptrend"]].copy()
            dataframe = pd.merge(dataframe, btc_slim, on="date", how="left")
            dataframe["btc_uptrend"] = dataframe["btc_uptrend"].ffill().fillna(False)
        else:
            dataframe["btc_uptrend"] = True  # backtesting fallback if BTC data unavailable

        return dataframe

    def populate_entry_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (
                (dataframe["ema_fast"] > dataframe["ema_slow"])
                & (dataframe["close"] <= dataframe["ema20"] * 1.01)
                & (dataframe["close"] >= dataframe["ema20"] * 0.97)
                & (dataframe["rsi"] > self.rsi_buy_threshold.value)
                & (dataframe["rsi"] < 65)
                & (dataframe["atr_pct"] > dataframe["atr_pct_median"] * 0.7)
                & (dataframe["volume"] > dataframe["vol_mean20"] * 0.8)
                & (dataframe["volume"] > 0)
                # NEW: no new entries unless the broader market is trending up
                & (dataframe["btc_uptrend"])
            ),
            "enter_long",
        ] = 1
        return dataframe

    def populate_exit_trend(self, dataframe: pd.DataFrame, metadata: dict) -> pd.DataFrame:
        dataframe.loc[
            (
                (dataframe["ema_fast"] < dataframe["ema_slow"])
                | (dataframe["rsi"] > 78)
                | (dataframe["adx_falling"] & dataframe["macd_hist_falling"] & (dataframe["rsi"] > 60))
                # NEW: if the broader market rolls over, start exiting
                # even if this specific pair still technically looks fine
                | (~dataframe["btc_uptrend"])
            ),
            "exit_long",
        ] = 1
        return dataframe

    def adjust_trade_position(self, trade: Trade, current_time: datetime, current_rate: float,
                               current_profit: float, min_stake, max_stake: float,
                               current_entry_rate: float, current_exit_rate: float,
                               current_entry_profit: float, current_exit_profit: float,
                               **kwargs):
        if current_profit >= self.partial_profit_trigger.value and trade.nr_of_successful_exits == 0:
            amount_to_sell = -(trade.amount * self.partial_exit_fraction.value)
            return amount_to_sell
        return None

    def custom_stoploss(self, pair: str, trade: Trade, current_time: datetime,
                         current_rate: float, current_profit: float, **kwargs) -> float:
        if trade.nr_of_successful_exits > 0:
            return -0.002
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return self.stoploss
        last_atr_pct = dataframe["atr_pct"].iloc[-1]
        dynamic_stop = -(last_atr_pct * self.atr_stop_mult.value)
        return max(dynamic_stop, self.stoploss)

    def custom_stake_amount(self, pair: str, current_time: datetime, current_rate: float,
                             proposed_stake: float, min_stake, max_stake: float,
                             leverage: float, entry_tag, side: str, **kwargs) -> float:
        dataframe, _ = self.dp.get_analyzed_dataframe(pair, self.timeframe)
        if dataframe.empty:
            return proposed_stake
        last_atr_pct = dataframe["atr_pct"].iloc[-1]
        stop_distance = max(last_atr_pct * self.atr_stop_mult.value, 0.01)
        total_capital = self.wallets.get_total_stake_amount()
        target_stake = (total_capital * self.risk_per_trade.value) / stop_distance
        return max(min(target_stake, max_stake), min_stake or 0)
