"""The adapter, driven through a fake freqtrade that calls its hooks the way
the real one does -- same method names, same argument shapes, same order.

What these hold: one decision however many passes see a signal; the
features recorded are the ones acted on; every way a signal can fail to
become a trade leaves a rejection with a reason; an executed entry is one
chain of events under one decision; the exchange refusing an order invents
no fill; an exit is its own decision on the same position; a restart finds
the decision that opened a position and invents none for a position it did
not see; and a hook that throws never reaches the trading loop.
"""

from __future__ import annotations

import sys
import types
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from app.learning import freqtrade_adapter
from app.learning.freqtrade_adapter import FreqtradeAdapter, LearningRPCHandler
from app.learning.outbox import SqliteOutbox
from app.learning.recorder import Recorder

TF = "4h"


def last_closed_candle() -> datetime:
    now = datetime.now(timezone.utc)
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % 14400, tz=timezone.utc) - timedelta(hours=4)


def frame(candle: datetime, **columns) -> pd.DataFrame:
    row = {"date": pd.Timestamp(candle), "open": 1.0, "high": 1.2, "low": 0.9, "close": 1.1,
           "volume": 10.0, "rsi": 61.2, "adx_falling": False, "enter_long": 0, "exit_long": 0,
           "enter_tag": None, "exit_tag": None}
    row.update(columns)
    return pd.DataFrame([row])


# -- a freqtrade made of cardboard ------------------------------------------
class Exceptions(types.SimpleNamespace):
    pass


def exceptions() -> Exceptions:
    class FreqtradeException(Exception): ...
    class OperationalException(FreqtradeException): ...
    class DependencyException(FreqtradeException): ...
    class PricingError(DependencyException): ...
    class ExchangeError(DependencyException): ...
    class InvalidOrderException(ExchangeError): ...
    class InsufficientFundsError(InvalidOrderException): ...
    class TemporaryError(ExchangeError): ...
    class DDosProtection(TemporaryError): ...
    return Exceptions(**{c.__name__: c for c in (
        FreqtradeException, OperationalException, DependencyException, PricingError, ExchangeError,
        InvalidOrderException, InsufficientFundsError, TemporaryError, DDosProtection)})


class State:
    def __init__(self, name):
        self.name = name


class Lock:
    def __init__(self, pair, reason="Cooldown period", side="*"):
        self.pair, self.reason, self.side = pair, reason, side
        self.lock_end_time_utc = datetime.now(timezone.utc) + timedelta(hours=8)


class Order:
    def __init__(self, order_id, ft_id, side, amount, price, status="open", tag=None):
        self.order_id, self.id, self.ft_order_side = order_id, ft_id, side
        self.safe_amount, self.safe_price, self.status = amount, price, status
        self.safe_filled = amount if status == "closed" else 0.0
        self.safe_remaining = 0.0 if status == "closed" else amount
        self.cost = amount * price
        self.ft_order_tag, self.order_type = tag, "limit"
        self.order_filled_utc = datetime.now(timezone.utc) if status == "closed" else None


class Trade:
    def __init__(self, id, pair, amount=10.0, custom=None):
        self.id, self.pair, self.amount = id, pair, amount
        self.is_short, self.open_rate, self.stake_amount = False, 1.0, 10.0
        self.enter_tag, self.strategy = "pullback", "Fake"
        self.open_date_utc = datetime.now(timezone.utc)
        self.entry_side, self.exit_side = "buy", "sell"
        self.custom = dict(custom or {})

    def get_custom_data(self, key, default=None):
        return self.custom.get(key, default)


class ExitCheck:
    def __init__(self, exit_type, reason=""):
        self.exit_type = types.SimpleNamespace(value=exit_type)
        self.exit_reason = reason or exit_type


def world(*, max_open_trades=6, min_stake=5.0):
    """Fresh classes for every test: the adapter patches them in place."""
    exc = exceptions()
    open_trades: list[Trade] = []
    custom_store: dict = {}

    class TradeModel:
        @staticmethod
        def get_open_trades():
            return list(open_trades)

    class PairLocks:
        global_lock = False
        locked: dict[str, Lock] = {}

        @classmethod
        def is_global_lock(cls, now=None, side="*"):
            return cls.global_lock

        @classmethod
        def get_pair_locks(cls, pair, now=None, side="*"):
            return [cls.locked[pair]] if pair in cls.locked else []

        @classmethod
        def get_pair_longest_lock(cls, pair, now=None, side="*"):
            if pair == "*":
                return Lock("*", "StoplossGuard") if cls.global_lock else None
            return cls.locked.get(pair)

    class CustomData:
        @staticmethod
        def set_custom_data(trade_id, key, value):
            custom_store.setdefault(trade_id, {})[key] = value

    class RPCHandlerBase:
        def __init__(self, rpc, config):
            self._rpc, self._config = rpc, config

    class RPCManager:
        def __init__(self, freqtrade):
            self.registered_modules = []
            self._rpc = None

        def send_msg(self, msg):
            for mod in self.registered_modules:
                mod.send_msg(msg)

    class Strategy:
        timeframe, stoploss, trailing_stop, position_adjustment_enable = TF, -0.06, True, True
        INTERFACE_VERSION = 3

        def __init__(self):
            self.signals: dict[str, tuple] = {}
            self.exit_signals: dict[str, tuple] = {}
            self.filled_calls = []

        def get_entry_signal(self, pair, timeframe, dataframe):
            return self.signals.get(pair, (None, None))

        def get_exit_signal(self, pair, timeframe, dataframe, is_short=None):
            return self.exit_signals.get(pair, (False, False, None))

        def is_pair_locked(self, pair, *, candle_date=None, side="*"):
            return pair in PairLocks.locked

        def order_filled(self, pair, trade, order, current_time, **kwargs):
            self.filled_calls.append(order.order_id)

    class StrategyResolver:
        @staticmethod
        def load_strategy(config=None):
            return Strategy()

    class Wallets:
        def __init__(self):
            self.available, self.refuse_stake, self.validated = 100.0, None, None

        def get_total_stake_amount(self):
            return 120.0

        def get_available_stake_amount(self):
            return self.available

        def get_trade_stake_amount(self, pair, max_open_trades, update=True):
            if self.refuse_stake:
                raise exc.DependencyException(self.refuse_stake)
            return 10.0

        def validate_stake_amount(self, pair, stake_amount, min_stake_amount, max_stake_amount, trade_amount):
            return self.validated if self.validated is not None else stake_amount

    class Exchange:
        def __init__(self):
            self.fail = None
            self.counter = 0
            self.status = "open"

        def create_order(self, *, pair, ordertype, side, amount, rate, leverage, time_in_force="GTC",
                         reduceOnly=False, initial_order=True):
            if self.fail is not None:
                raise self.fail
            self.counter += 1
            return {"id": f"ex-{self.counter}", "status": self.status, "filled": 0, "remaining": amount,
                    "average": rate, "price": rate, "cost": amount * rate, "type": ordertype, "side": side,
                    "datetime": "now"}

    class DataProvider:
        def __init__(self):
            self.frames: dict[str, pd.DataFrame] = {}

        def get_analyzed_dataframe(self, pair, timeframe):
            df = self.frames.get(pair, pd.DataFrame())
            return df, datetime.now(timezone.utc)

    class Bot:
        def __init__(self):
            self.state = State("RUNNING")
            self.config = {"max_open_trades": max_open_trades, "timeframe": TF, "strategy": "Fake"}
            self.strategy = StrategyResolver.load_strategy(self.config)
            self.wallets, self.exchange, self.dataprovider = Wallets(), Exchange(), DataProvider()
            self.rpc = RPCManager(self)
            self.active_pair_whitelist: list[str] = []
            self.next_id, self.orders = 1, {}
            self.min_stake = min_stake

        def get_free_open_trades(self):
            return self.config["max_open_trades"] - len(open_trades)

        def startup(self):
            return None

        def process(self):
            if self.state.name == "RUNNING" and self.get_free_open_trades() > 0:
                self.enter_positions(self.get_free_open_trades())

        def enter_positions(self, free):
            whitelist = [p for p in self.active_pair_whitelist if p not in {t.pair for t in open_trades}]
            if PairLocks.is_global_lock(side="*"):
                return 0
            for pair in whitelist:
                if free <= 0:
                    break
                try:
                    if self.create_trade(pair):
                        free -= 1
                except exc.DependencyException:
                    pass
            return 0

        def create_trade(self, pair):
            if not self.get_free_open_trades():
                return False
            df, _ = self.dataprovider.get_analyzed_dataframe(pair, self.strategy.timeframe)
            nowtime = df.iloc[-1]["date"] if len(df) else None
            signal, tag = self.strategy.get_entry_signal(pair, self.strategy.timeframe, df)
            if not signal:
                return False
            if self.strategy.is_pair_locked(pair, candle_date=nowtime, side=signal):
                return False
            stake = self.wallets.get_trade_stake_amount(pair, self.config["max_open_trades"])
            return self.execute_entry(pair, stake, enter_tag=tag, is_short=(signal == "short"))

        def execute_entry(self, pair, stake_amount, price=None, *, is_short=False, ordertype=None,
                          enter_tag=None, trade=None, mode="initial", leverage_=None):
            stake = self.wallets.validate_stake_amount(pair=pair, stake_amount=stake_amount,
                                                       min_stake_amount=self.min_stake,
                                                       max_stake_amount=1000.0, trade_amount=None)
            if not stake:
                return False
            order = self.exchange.create_order(pair=pair, ordertype="limit", side="buy", amount=stake,
                                               rate=1.0, leverage=1.0, initial_order=trade is None)
            if trade is None:
                trade = Trade(self.next_id, pair, amount=stake)
                self.next_id += 1
                open_trades.append(trade)
            self.orders[order["id"]] = Order(order["id"], len(self.orders) + 1, "buy", stake, 1.0, tag=enter_tag)
            self.rpc.send_msg({"type": "entry", "trade_id": trade.id, "pair": pair, "sub_trade": mode == "pos_adjust",
                               "stake_amount": stake, "amount": stake, "order_rate": 1.0, "enter_tag": enter_tag})
            return True

        def execute_trade_exit(self, trade, limit, exit_check, *, exit_tag=None, ordertype=None,
                               sub_trade_amt=None, skip_custom_exit_price=False):
            order = self.exchange.create_order(pair=trade.pair, ordertype="limit", side="sell",
                                               amount=sub_trade_amt or trade.amount, rate=limit, leverage=1.0,
                                               initial_order=False)
            self.orders[order["id"]] = Order(order["id"], len(self.orders) + 1, "sell", trade.amount, limit)
            self.rpc.send_msg({"type": "exit", "trade_id": trade.id, "pair": trade.pair,
                               "exit_reason": exit_tag or exit_check.exit_reason, "sub_trade": bool(sub_trade_amt)})
            return True

        # what freqtrade does after a fill: the strategy callback, then the RPC message
        def fill(self, order_id, *, final_exit=False):
            order = self.orders[order_id]
            order.status, order.safe_filled, order.safe_remaining = "closed", order.safe_amount, 0.0
            trade = next(t for t in open_trades if t.pair == self._pair_of(order_id))
            self.strategy.order_filled(pair=trade.pair, trade=trade, order=order,
                                       current_time=datetime.now(timezone.utc))
            if order.ft_order_side == "buy":
                self.rpc.send_msg({"type": "entry_fill", "trade_id": trade.id, "pair": trade.pair,
                                   "sub_trade": False, "amount": order.safe_amount, "open_rate": order.safe_price})
            else:
                self.rpc.send_msg({"type": "exit_fill", "trade_id": trade.id, "pair": trade.pair,
                                   "is_final_exit": final_exit, "sub_trade": not final_exit,
                                   "amount": order.safe_amount, "close_rate": order.safe_price,
                                   "profit_ratio": 0.05, "exit_reason": "exit_signal"})
                if final_exit:
                    open_trades.remove(trade)

        def _pair_of(self, order_id):
            for trade in open_trades:
                return trade.pair
            raise AssertionError("no open trade")

    return types.SimpleNamespace(
        exc=exc, open_trades=open_trades, custom_store=custom_store, TradeModel=TradeModel,
        PairLocks=PairLocks, CustomData=CustomData, RPCHandlerBase=RPCHandlerBase, RPCManager=RPCManager,
        Strategy=Strategy, StrategyResolver=StrategyResolver, Wallets=Wallets, Exchange=Exchange, Bot=Bot,
    )


@pytest.fixture
def rig(tmp_path):
    w = world()
    box = SqliteOutbox(str(tmp_path / "outbox.sqlite"))
    identity = {"bot_name": "bot-test", "bot_instance_id": "bot-uuid", "owner_id": "owner-1", "account_id": None}
    recorder = Recorder(box, identity, environment="staging", exchange="kucoin", strategy_id="Fake",
                        provenance={"strategy_code_hash": "abc", "feature_set_version": "t"},
                        log=lambda *_: None)
    adapter = FreqtradeAdapter(recorder, Trade=w.TradeModel, PairLocks=w.PairLocks, custom_data=w.CustomData,
                               exceptions=w.exc, rpc_handler_base=w.RPCHandlerBase, version="2026.7",
                               bot_name="bot-test", stake_currency="USDT")
    adapter.patch(FreqtradeBot=w.Bot, Wallets=w.Wallets, Exchange=w.Exchange, IStrategy=w.Strategy,
                  RPCManager=w.RPCManager, StrategyResolver=w.StrategyResolver)
    bot = w.Bot()
    candle = last_closed_candle()
    w.recorder, w.adapter, w.bot, w.box, w.candle = recorder, adapter, bot, box, candle
    yield w
    box.close()


def signal_on(w, pair, tag="pullback"):
    w.bot.active_pair_whitelist.append(pair)
    w.bot.dataprovider.frames[pair] = frame(w.candle, enter_long=1, enter_tag=tag)
    w.bot.strategy.signals[pair] = ("long", tag)


def rows(w, kind):
    import json
    return [json.loads(r.payload) for r in w.box.claim(500) if r.kind == kind]


def events(w, decision_id=None):
    return [e for e in rows(w, "event") if decision_id is None or e["decision_id"] == decision_id]


# -- the tests ------------------------------------------------------------
def test_one_decision_however_many_passes_see_the_signal(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.PairLocks.locked["TRX/USDT"] = Lock("TRX/USDT")
    for _ in range(3):
        w.bot.process()
    decisions = rows(w, "decision")
    assert len(decisions) == 1
    assert decisions[0]["symbol"] == "TRX/USDT" and decisions[0]["decision_kind"] == "entry"
    kinds = [e["event_type"] for e in events(w)]
    assert kinds == ["signal_generated", "signal_rejected"]
    rejection = events(w)[1]
    assert rejection["rejection_code"] == "PAIR_LOCKED" and rejection["rejection_stage"] == "bot"
    assert rejection["payload"]["lock"]["reason"] == "Cooldown period"
    assert w.recorder.repeat_count(decisions[0]["decision_id"], "PAIR_LOCKED") == 3
    assert w.recorder.health()["rejections"] == {"PAIR_LOCKED": 3}
    assert w.recorder.health()["adapter_errors"] == 0


def test_the_features_recorded_are_the_ones_acted_on(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.dataprovider.frames["TRX/USDT"] = frame(w.candle, enter_long=1, enter_tag="pullback", rsi=47.5)
    w.bot.process()
    [decision] = rows(w, "decision")
    assert decision["feature_snapshot"] == {"adx_falling": False, "rsi": 47.5}
    assert decision["market_context"]["candle_open"] == w.candle.isoformat()
    assert decision["quarantined"] is False
    assert decision["entry_tag"] == "pullback"
    assert decision["provenance"]["strategy_code_hash"] == "abc"
    assert decision["portfolio_snapshot"]["available_stake"] == 100.0
    assert decision["risk_snapshot"]["free_slots"] == 6  # as it stood when the decision was made


def test_a_full_book_records_every_skipped_signal_with_the_reason(tmp_path):
    w = world(max_open_trades=1)
    rig_w = _rig_for(w, tmp_path)
    w.open_trades.append(Trade(99, "ETH/USDT"))
    signal_on(w, "TRX/USDT")
    signal_on(w, "XRP/USDT")
    w.bot.process()  # enter_positions is never reached: no free slot
    decisions = rows(w, "decision")
    assert sorted(d["symbol"] for d in decisions) == ["TRX/USDT", "XRP/USDT"]
    assert all(d["provenance"]["derivation"] == "whitelist_scan" for d in decisions)
    rejections = [e for e in events(w) if e["event_type"] == "signal_rejected"]
    assert {e["rejection_code"] for e in rejections} == {"MAX_OPEN_TRADES"}
    assert rejections[0]["payload"]["open_positions"] == 1
    assert [e["event_type"] for e in events(w) if e["event_source"] == "learning_adapter"][:2] == \
        ["signal_generated", "signal_rejected"]
    rig_w.close()


def _rig_for(w, tmp_path):
    box = SqliteOutbox(str(tmp_path / "o2.sqlite"))
    identity = {"bot_name": "bot-test", "bot_instance_id": "bot-uuid", "owner_id": "owner-1"}
    w.recorder = Recorder(box, identity, environment="staging", exchange="kucoin", strategy_id="Fake",
                          provenance={"strategy_code_hash": "abc"}, log=lambda *_: None)
    w.adapter = FreqtradeAdapter(w.recorder, Trade=w.TradeModel, PairLocks=w.PairLocks, custom_data=w.CustomData,
                                 exceptions=w.exc, rpc_handler_base=w.RPCHandlerBase, bot_name="bot-test",
                                 stake_currency="USDT")
    w.adapter.patch(FreqtradeBot=w.Bot, Wallets=w.Wallets, Exchange=w.Exchange, IStrategy=w.Strategy,
                    RPCManager=w.RPCManager, StrategyResolver=w.StrategyResolver)
    w.bot, w.box, w.candle = w.Bot(), box, last_closed_candle()
    return box


def test_a_global_lock_an_open_position_and_a_pause_are_told_apart(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    signal_on(w, "ETH/USDT")
    w.open_trades.append(Trade(5, "ETH/USDT"))
    w.PairLocks.global_lock = True
    w.bot.process()
    by_symbol = {d["symbol"]: d["decision_id"] for d in rows(w, "decision")}
    codes = {e["symbol"]: e["rejection_code"] for e in events(w) if e["event_type"] == "signal_rejected"}
    assert codes == {"TRX/USDT": "GLOBAL_PAIRLOCK", "ETH/USDT": "POSITION_ALREADY_OPEN"}
    lock_event = next(e for e in events(w) if e["rejection_code"] == "GLOBAL_PAIRLOCK")
    assert lock_event["payload"]["lock"]["reason"] == "StoplossGuard"
    open_event = next(e for e in events(w) if e["rejection_code"] == "POSITION_ALREADY_OPEN")
    assert open_event["payload"]["ft_trade_id"] == 5

    # A paused bot manages what it holds and opens nothing: every signal says so.
    w.PairLocks.global_lock = False
    w.bot.state = State("PAUSED")
    signal_on(w, "XRP/USDT")
    w.bot.process()
    paused = [e for e in events(w) if e["rejection_code"] == "BOT_PAUSED"]
    assert [e["symbol"] for e in paused] == ["XRP/USDT"]
    assert len(rows(w, "decision")) == 3  # the two earlier ones were not repeated


def test_a_stake_below_the_minimum_is_a_rejection_not_a_silent_no(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.wallets.validated = 0
    w.bot.min_stake = 50.0  # 10 * 1.3 < 50: freqtrade gives up on the trade
    w.bot.process()
    [rejection] = [e for e in events(w) if e["event_type"] == "signal_rejected"]
    assert rejection["rejection_code"] == "MIN_NOTIONAL"
    assert rejection["payload"]["min_stake_amount"] == 50.0
    assert not [e for e in events(w) if e["event_type"].startswith("order_")]

    signal_on(w, "XRP/USDT")
    w.bot.wallets.available = 20.0
    w.bot.min_stake = 30.0  # the minimum is more than what is free
    w.bot.process()
    [balance] = [e for e in events(w) if e["symbol"] == "XRP/USDT" and e["event_type"] == "signal_rejected"]
    assert balance["rejection_code"] == "INSUFFICIENT_BALANCE"

    signal_on(w, "ADA/USDT")
    w.bot.wallets.validated = None
    w.bot.wallets.refuse_stake = "Available balance (3 USDT) is lower than stake amount (10 USDT)"
    w.bot.process()
    [refused] = [e for e in events(w) if e["symbol"] == "ADA/USDT" and e["event_type"] == "signal_rejected"]
    assert refused["rejection_code"] == "INSUFFICIENT_BALANCE"
    assert "lower than stake amount" in refused["payload"]["message"]


def test_an_executed_entry_is_one_chain_of_events_under_one_decision(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.process()
    [decision] = rows(w, "decision")
    did = decision["decision_id"]
    assert [e["event_type"] for e in events(w, did)] == [
        "signal_generated", "bot_instruction_created", "order_submitted", "order_acknowledged"]
    ack = events(w, did)[3]
    assert ack["exchange_order_id"] == "ex-1" and ack["payload"]["status"] == "open"
    assert w.recorder.trade(1)["origin_decision_id"] == did
    assert w.custom_store[1] == {"learning_decision_id": did, "learning_position_id": decision["position_id"]}

    w.bot.fill("ex-1")
    tail = [e["event_type"] for e in events(w, did)][4:]
    assert tail == ["fill_completed", "position_opened"]
    fill = events(w, did)[4]
    assert fill["exchange_order_id"] == "ex-1" and fill["ft_order_id"] == 1 and fill["ft_trade_id"] == 1
    assert fill["payload"]["filled"] == 10.0
    assert {e["position_id"] for e in events(w, did)} == {decision["position_id"]}
    assert w.bot.strategy.filled_calls == ["ex-1"], "the strategy's own callback still ran"
    assert w.recorder.health()["adapter_errors"] == 0


def test_an_exchange_refusal_is_recorded_and_no_fill_is_invented(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.exchange.fail = w.exc.InsufficientFundsError("Insufficient funds to create limit buy order")
    w.bot.process()  # enter_positions swallows the DependencyException, as freqtrade's does
    [decision] = rows(w, "decision")
    kinds = [e["event_type"] for e in events(w, decision["decision_id"])]
    assert kinds == ["signal_generated", "bot_instruction_created", "order_submitted", "order_rejected"]
    rejected = events(w, decision["decision_id"])[3]
    assert rejected["rejection_code"] == "INSUFFICIENT_BALANCE" and rejected["rejection_stage"] == "exchange"
    assert "Insufficient funds" in rejected["payload"]["exchange_message"]
    assert w.open_trades == [] and w.recorder.trade(1) is None
    assert w.recorder.is_terminal(decision["decision_id"])


def test_the_original_exception_still_reaches_freqtrade(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.exchange.fail = w.exc.OperationalException("exchange down")
    with pytest.raises(w.exc.OperationalException):
        w.bot.create_trade("TRX/USDT")
    [rejected] = [e for e in events(w) if e["event_type"] == "order_rejected"]
    assert rejected["rejection_code"] == "SYSTEM_ERROR" and rejected["rejection_stage"] == "system"


def test_an_exit_is_its_own_decision_on_the_same_position(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.process()
    w.bot.fill("ex-1")
    [entry] = rows(w, "decision")
    trade = w.open_trades[0]

    w.bot.strategy.exit_signals["TRX/USDT"] = (False, True, "take_profit")
    w.bot.strategy.get_exit_signal("TRX/USDT", TF, w.bot.dataprovider.frames["TRX/USDT"], is_short=False)
    w.bot.execute_trade_exit(trade, 1.2, ExitCheck("exit_signal"), exit_tag="take_profit")
    w.bot.fill("ex-2", final_exit=True)

    entry_d, exit_d = rows(w, "decision")
    assert exit_d["decision_kind"] == "exit" and exit_d["strategy_intent"] == "exit_long"
    assert exit_d["position_id"] == entry["position_id"]
    assert exit_d["exit_reason"] == "take_profit"
    assert exit_d["decision_id"] != entry["decision_id"]
    assert [e["event_type"] for e in events(w, exit_d["decision_id"])] == [
        "exit_signal_generated", "bot_instruction_created", "order_submitted", "order_acknowledged",
        "fill_completed", "position_closed"]
    assert w.recorder.trade_for_pair("TRX/USDT") is None  # closed


def test_a_forced_exit_has_no_strategy_signal(rig):
    w = rig
    signal_on(w, "TRX/USDT")
    w.bot.process()
    w.bot.fill("ex-1")
    trade = w.open_trades[0]
    w.bot.execute_trade_exit(trade, 1.05, ExitCheck("force_exit"), ordertype="market")
    _, forced = rows(w, "decision")
    assert forced["provenance"]["trigger"] == "force_exit"
    assert forced["exit_reason"] == "force_exit"
    kinds = [e["event_type"] for e in events(w, forced["decision_id"])]
    assert kinds[0] == "bot_instruction_created"
    assert "exit_signal_generated" not in kinds


def test_a_restart_finds_the_decision_that_opened_a_position_and_invents_none(rig):
    w = rig
    w.open_trades.append(Trade(7, "TRX/USDT", custom={"learning_decision_id": "d-old", "learning_position_id": "p-old"}))
    w.open_trades.append(Trade(8, "XRP/USDT"))
    w.bot.startup()
    linked = w.recorder.trade(7)
    assert (linked["pair"], linked["position_id"], linked["origin_decision_id"]) == ("TRX/USDT", "p-old", "d-old")
    assert w.recorder.trade(8)["position_id"] == "ft:bot-test:8"
    assert rows(w, "decision") == []
    [unlinked] = events(w)
    assert unlinked["event_type"] == "unlinked_position_observed"
    assert unlinked["decision_id"] is None and unlinked["ft_trade_id"] == 8
    # the exit of the old position attaches to what opened it, through the fill hook
    w.bot.startup()  # again: the unlinked event is not repeated
    assert len(events(w)) == 1


def test_a_hook_that_throws_never_reaches_the_trading_loop(rig, monkeypatch):
    w = rig
    signal_on(w, "TRX/USDT")

    def explode(*args, **kwargs):
        raise RuntimeError("adapter bug")

    monkeypatch.setattr(w.recorder, "open_decision", explode)
    w.bot.process()  # returns normally
    assert w.open_trades and w.open_trades[0].pair == "TRX/USDT", "the trade still happened"
    assert w.recorder.health()["adapter_errors"] >= 1
    assert "adapter bug" in w.recorder.health()["last_error"]


def test_bot_status_and_protections_are_recorded_without_a_decision(rig):
    w = rig
    w.bot.rpc.send_msg({"type": "status", "status": "running"})
    w.bot.rpc.send_msg({"type": "protection_trigger", "pair": "TRX/USDT", "reason": "Cooldown period",
                        "lock_end_time": "2026-09-17 16:00:00", "side": "*"})
    w.bot.rpc.send_msg({"type": "status", "status": "running"})  # repeated: same key
    recorded = events(w)
    assert [(e["event_type"], e["decision_id"]) for e in recorded] == [("bot_status", None), ("risk_decision", None)]
    assert recorded[1]["symbol"] == "TRX/USDT"


def test_the_rpc_handler_looks_like_one_of_freqtrades(rig):
    w = rig
    [handler] = w.bot.rpc.registered_modules
    assert isinstance(handler, w.RPCHandlerBase) and isinstance(handler, LearningRPCHandler)
    assert handler.name == "learning" and handler._config == w.bot.config
    calls = []
    w.adapter.on_cleanup = lambda: calls.append("flushed")
    handler.cleanup()
    assert calls == ["flushed"]


def test_install_hooks_the_real_module_names(monkeypatch, tmp_path):
    w = world()
    def module(name, **attrs):
        m = types.ModuleType(name)
        m.__dict__.update(attrs)
        return m

    modules = {
        "freqtrade": module("freqtrade", __version__="2026.7", __path__=[]),
        "freqtrade.exceptions": module("freqtrade.exceptions", **vars(w.exc)),
        "freqtrade.exchange": module("freqtrade.exchange", Exchange=w.Exchange),
        "freqtrade.freqtradebot": module("freqtrade.freqtradebot", FreqtradeBot=w.Bot),
        "freqtrade.persistence": module("freqtrade.persistence", PairLocks=w.PairLocks, Trade=w.TradeModel),
        "freqtrade.persistence.custom_data": module("freqtrade.persistence.custom_data", CustomDataWrapper=w.CustomData),
        "freqtrade.resolvers": module("freqtrade.resolvers", StrategyResolver=w.StrategyResolver),
        "freqtrade.rpc": module("freqtrade.rpc", RPCHandler=w.RPCHandlerBase),
        "freqtrade.rpc.rpc_manager": module("freqtrade.rpc.rpc_manager", RPCManager=w.RPCManager),
        "freqtrade.strategy.interface": module("freqtrade.strategy.interface", IStrategy=w.Strategy),
        "freqtrade.wallets": module("freqtrade.wallets", Wallets=w.Wallets),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    box = SqliteOutbox(str(tmp_path / "i.sqlite"))
    recorder = Recorder(box, {"bot_name": "b"}, environment="staging", exchange="kucoin", strategy_id="Fake",
                        log=lambda *_: None)
    adapter = freqtrade_adapter.install(recorder, bot_name="b", stake_currency="USDT")
    assert set(adapter.patched) >= {
        "Strategy.get_entry_signal", "Strategy.get_exit_signal", "Strategy.is_pair_locked",
        "Wallets.get_trade_stake_amount", "Wallets.validate_stake_amount", "Bot.create_trade",
        "Bot.execute_entry", "Bot.execute_trade_exit", "Bot.process", "Bot.startup", "Exchange.create_order",
        "RPCManager.__init__", "StrategyResolver.load_strategy",
    }
    assert adapter.version == "2026.7"
    bot = w.Bot()
    assert [m.name for m in bot.rpc.registered_modules] == ["learning"]
    assert "strategy.order_filled" in adapter.patched
    box.close()
