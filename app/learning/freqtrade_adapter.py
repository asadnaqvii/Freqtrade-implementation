"""Where the Learning Module meets freqtrade: the only file that imports it.

Every hook wraps a freqtrade method under three rules: the original always
runs; its result and its exceptions pass through unchanged; and anything the
hook itself gets wrong is counted, never raised. The strategy files are not
touched -- the hooks sit on freqtrade's own classes, and on the loaded
strategy instance for the one callback (`order_filled`) that must compose
with whatever the strategy defines.

The hooks, in the order a trade happens:

  IStrategy.get_entry_signal       the strategy wants in: open the decision
  IStrategy.is_pair_locked         locked: rejected, with the lock
  Wallets.get_trade_stake_amount   no free balance: rejected
  Wallets.validate_stake_amount    stake too small for the venue: rejected
  FreqtradeBot.execute_entry       the bot commits: the instruction, then the order
  Exchange.create_order            submitted, then acknowledged or refused
  RPC ENTRY / ENTRY_FILL           the trade row exists: link it; the position opened
  strategy.order_filled            fills, with their order ids
  IStrategy.get_exit_signal        the strategy wants out
  FreqtradeBot.execute_trade_exit  every exit: signal, ROI, stop, forced, partial
  RPC EXIT_FILL                    the position closed
  FreqtradeBot.process             after each pass: the signals the bot skipped, and why
  FreqtradeBot.startup             the trades already open: linked, or declared unlinked
"""

from __future__ import annotations

import functools
from datetime import datetime
from typing import Any, Callable

from app.learning import provenance as provenance_module
from app.learning import snapshots
from app.learning.enums import EventType, RejectionCode, RejectionStage
from app.learning.recorder import Context, Recorder

#: The two keys written into freqtrade's own trade_custom_data table: the
#: correlation store that survives a redeploy (the outbox file does not).
CUSTOM_DATA_DECISION = "learning_decision_id"
CUSTOM_DATA_POSITION = "learning_position_id"


def _arg(name: str, index: int | None, args: tuple, kwargs: dict, default: Any = None) -> Any:
    if name in kwargs:
        return kwargs[name]
    if index is not None and len(args) > index:
        return args[index]
    return default


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) and value and value != "nan" else None


class FreqtradeAdapter:
    def __init__(self, recorder: Recorder, *, Trade: Any, PairLocks: Any, custom_data: Any,
                 exceptions: Any, rpc_handler_base: type | None = None, version: str | None = None,
                 bot_name: str, stake_currency: str, on_cleanup: Callable[[], Any] | None = None,
                 log: Callable[[str], None] = print) -> None:
        self.recorder = recorder
        self.Trade = Trade
        self.PairLocks = PairLocks
        self.custom_data = custom_data
        self.exc = exceptions
        self.rpc_handler_base = rpc_handler_base or object
        self.version = version
        self.bot_name = bot_name
        self.stake_currency = stake_currency
        self.on_cleanup = on_cleanup or (lambda: None)
        self.log = log
        self.bot: Any = None
        self.strategy: Any = None
        self.config: dict = {}
        self.patched: list[str] = []

    # -- the safety net ----------------------------------------------------
    def _guard(self, where: str, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        try:
            return fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - counted; trading has moved on
            self.recorder.note_error(where, exc)
            return None

    def wrap(self, original: Callable, name: str, *, before: Callable | None = None,
             after: Callable | None = None, on_error: Callable | None = None) -> Callable:
        """A method wrapper that passes results and exceptions through untouched."""
        adapter = self

        @functools.wraps(original)
        def wrapper(instance, *args, **kwargs):
            if before is not None:
                adapter._guard(f"{name}.before", before, instance, *args, **kwargs)
            try:
                result = original(instance, *args, **kwargs)
            except BaseException as exc:
                if on_error is not None:
                    adapter._guard(f"{name}.error", on_error, instance, exc, *args, **kwargs)
                raise
            if after is not None:
                adapter._guard(f"{name}.after", after, instance, result, *args, **kwargs)
            return result

        wrapper.__learning_wrapped__ = original  # type: ignore[attr-defined]
        return wrapper

    def _patch(self, cls: Any, name: str, **hooks: Callable | None) -> None:
        original = getattr(cls, name, None)
        if original is None or hasattr(original, "__learning_wrapped__"):
            return
        setattr(cls, name, self.wrap(original, f"{cls.__name__}.{name}", **hooks))
        self.patched.append(f"{cls.__name__}.{name}")

    def patch(self, *, FreqtradeBot: Any = None, Wallets: Any = None, Exchange: Any = None,
              IStrategy: Any = None, RPCManager: Any = None, StrategyResolver: Any = None) -> list[str]:
        if IStrategy is not None:
            self._patch(IStrategy, "get_entry_signal", after=self.after_get_entry_signal)
            self._patch(IStrategy, "get_exit_signal", after=self.after_get_exit_signal)
            self._patch(IStrategy, "is_pair_locked", after=self.after_is_pair_locked)
        if Wallets is not None:
            self._patch(Wallets, "get_trade_stake_amount", on_error=self.on_error_get_trade_stake_amount)
            self._patch(Wallets, "validate_stake_amount", after=self.after_validate_stake_amount)
        if FreqtradeBot is not None:
            self._patch(FreqtradeBot, "create_trade", after=self.after_create_trade,
                        on_error=self.on_error_create_trade)
            self._patch(FreqtradeBot, "execute_entry", before=self.before_execute_entry,
                        after=self.after_execute_entry, on_error=self.on_error_execute_entry)
            self._patch(FreqtradeBot, "execute_trade_exit", before=self.before_execute_trade_exit,
                        after=self.after_execute_trade_exit, on_error=self.on_error_execute_trade_exit)
            self._patch(FreqtradeBot, "process", after=self.after_process)
            self._patch(FreqtradeBot, "startup", after=self.after_startup)
        if Exchange is not None:
            self._patch(Exchange, "create_order", before=self.before_create_order,
                        after=self.after_create_order, on_error=self.on_error_create_order)
        if RPCManager is not None:
            self._patch(RPCManager, "__init__", after=self.after_rpc_manager_init)
        if StrategyResolver is not None:
            self._patch_load_strategy(StrategyResolver)
        return list(self.patched)

    def _patch_load_strategy(self, StrategyResolver: Any) -> None:
        original = StrategyResolver.__dict__.get("load_strategy")
        if original is None or hasattr(original, "__learning_wrapped__"):
            return
        plain = original.__func__ if isinstance(original, staticmethod) else original
        adapter = self

        @functools.wraps(plain)
        def load_strategy(config=None):
            strategy = plain(config)
            adapter._guard("StrategyResolver.load_strategy.after", adapter.after_load_strategy,
                           strategy, config or {})
            return strategy

        load_strategy.__learning_wrapped__ = plain  # type: ignore[attr-defined]
        StrategyResolver.load_strategy = staticmethod(load_strategy)
        self.patched.append("StrategyResolver.load_strategy")

    # -- what the bot holds and what constrains it ---------------------------
    def _timeframe(self) -> str:
        for source in (self.strategy, getattr(self.bot, "strategy", None)):
            timeframe = getattr(source, "timeframe", None)
            if timeframe:
                return str(timeframe)
        return str(self.config.get("timeframe") or "4h")

    def _row_for(self, pair: str) -> dict | None:
        provider = getattr(self.bot, "dataprovider", None)
        if provider is None:
            return None
        dataframe, _ = provider.get_analyzed_dataframe(pair, self._timeframe())
        return snapshots.last_row(dataframe)

    def _portfolio(self) -> dict:
        wallets = getattr(self.bot, "wallets", None)
        if wallets is None:
            return {}
        return snapshots.portfolio_snapshot(wallets=wallets, open_trades=self.Trade.get_open_trades,
                                            stake_currency=self.stake_currency)

    def _risk(self, pair: str) -> dict:
        strategy = self.strategy or getattr(self.bot, "strategy", None)
        return snapshots.risk_snapshot(
            pair_locks=lambda: list(self.PairLocks.get_pair_locks(pair, None, side="*")),
            global_lock=lambda: bool(self.PairLocks.is_global_lock(side="*")),
            max_open_trades=self.config.get("max_open_trades"),
            free_slots=lambda: self.bot.get_free_open_trades() if self.bot is not None else None,
            stoploss=getattr(strategy, "stoploss", None),
            trailing_stop=getattr(strategy, "trailing_stop", None),
            position_adjustment_enable=getattr(strategy, "position_adjustment_enable", None),
            protections=self.config.get("protections"),
        )

    def _reject(self, ctx: Context, code: str, stage: str, payload: dict | None = None) -> None:
        self.recorder.reject(ctx.decision_id, code, stage, payload=payload)
        ctx.terminal = True

    def classify(self, exc: BaseException) -> tuple[str, str]:
        """(rejection_code, stage) for an exception freqtrade raised on the way to an order."""
        def is_a(name: str) -> bool:
            cls = getattr(self.exc, name, None)
            return isinstance(cls, type) and isinstance(exc, cls)

        message = str(exc).lower()
        if is_a("InsufficientFundsError"):
            return RejectionCode.INSUFFICIENT_BALANCE.value, RejectionStage.EXCHANGE.value
        if is_a("InvalidOrderException"):
            if "notional" in message or "min" in message:
                return RejectionCode.MIN_NOTIONAL.value, RejectionStage.EXCHANGE.value
            if "price" in message:
                return RejectionCode.INVALID_PRICE.value, RejectionStage.EXCHANGE.value
            if any(word in message for word in ("amount", "quantity", "size", "lot")):
                return RejectionCode.INVALID_QUANTITY.value, RejectionStage.EXCHANGE.value
            return RejectionCode.EXCHANGE_REJECTED.value, RejectionStage.EXCHANGE.value
        if is_a("DDosProtection") or is_a("TemporaryError"):
            return RejectionCode.EXCHANGE_ERROR.value, RejectionStage.EXCHANGE.value
        if is_a("PricingError"):
            return RejectionCode.INVALID_PRICE.value, RejectionStage.BOT.value
        if is_a("OperationalException"):
            return RejectionCode.SYSTEM_ERROR.value, RejectionStage.SYSTEM.value
        if is_a("DependencyException"):
            return RejectionCode.UNKNOWN.value, RejectionStage.BOT.value
        return RejectionCode.SYSTEM_ERROR.value, RejectionStage.SYSTEM.value

    # -- the strategy speaks ---------------------------------------------------
    def after_get_entry_signal(self, strategy, result, *args, **kwargs) -> None:
        signal, tag = result if isinstance(result, tuple) and len(result) == 2 else (None, None)
        if not signal:
            return
        pair = _arg("pair", 0, args, kwargs)
        timeframe = str(_arg("timeframe", 1, args, kwargs) or self._timeframe())
        row = snapshots.last_row(_arg("dataframe", 2, args, kwargs))
        intent = "enter_short" if str(getattr(signal, "value", signal)).lower() == "short" else "enter_long"
        opened = self.recorder.open_decision(
            kind="entry", intent=intent, symbol=pair, timeframe=timeframe,
            candle=snapshots.candle_open(row), row=row, portfolio=self._portfolio,
            risk=lambda: self._risk(pair), entry_tag=_text(tag),
        )
        if opened is None:
            return
        if opened.new:
            self.recorder.record_event(
                opened.decision_id, EventType.SIGNAL_GENERATED,
                key_time=self.recorder.info(opened.decision_id).get("candle"),
                payload={"intent": intent, "entry_tag": _text(tag),
                         "candle_open": snapshots.sanitise(row.get("date")) if row else None},
                event_source="freqtrade_callback",
            )
        self.recorder.begin(pair, opened.decision_id, "entry", position_id=opened.position_id)

    def after_get_exit_signal(self, strategy, result, *args, **kwargs) -> None:
        if not (isinstance(result, tuple) and len(result) == 3 and result[1]):
            return
        pair = _arg("pair", 0, args, kwargs)
        timeframe = str(_arg("timeframe", 1, args, kwargs) or self._timeframe())
        row = snapshots.last_row(_arg("dataframe", 2, args, kwargs))
        is_short = bool(_arg("is_short", 3, args, kwargs, False))
        trade_id = self.recorder.trade_for_pair(pair)
        opened = self.recorder.open_decision(
            kind="exit", intent="exit_short" if is_short else "exit_long", symbol=pair,
            timeframe=timeframe, candle=snapshots.candle_open(row), row=row,
            portfolio=self._portfolio, risk=lambda: self._risk(pair),
            exit_reason=_text(result[2]) or "exit_signal", seq=int(trade_id or 0),
            ft_trade_id=trade_id, trigger="exit_signal",
        )
        if opened is not None and opened.new:
            self.recorder.record_event(
                opened.decision_id, EventType.EXIT_SIGNAL_GENERATED,
                key_time=self.recorder.info(opened.decision_id).get("candle"),
                payload={"exit_tag": _text(result[2]),
                         "candle_open": snapshots.sanitise(row.get("date")) if row else None},
                ft_trade_id=trade_id, event_source="freqtrade_callback",
            )

    def after_is_pair_locked(self, strategy, result, *args, **kwargs) -> None:
        if not result:
            return
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is None or ctx.kind != "entry" or ctx.terminal:
            return
        side = kwargs.get("side", "*")
        lock = self._guard("pair_lock", self.PairLocks.get_pair_longest_lock, pair,
                           kwargs.get("candle_date"), side)
        self._reject(ctx, RejectionCode.PAIR_LOCKED.value, RejectionStage.BOT.value,
                     {"lock": snapshots.lock_info(lock) if lock else None, "side": side})

    # -- the bot weighs it -----------------------------------------------------
    def on_error_get_trade_stake_amount(self, wallets, exc, *args, **kwargs) -> None:
        dependency = getattr(self.exc, "DependencyException", None)
        if not (isinstance(dependency, type) and isinstance(exc, dependency)):
            return
        ctx = self.recorder.context(_arg("pair", 0, args, kwargs))
        if ctx is not None and not ctx.terminal:
            self._reject(ctx, RejectionCode.INSUFFICIENT_BALANCE.value, RejectionStage.BOT.value,
                         {"message": str(exc)[:300]})

    def after_validate_stake_amount(self, wallets, result, *args, **kwargs) -> None:
        if result:
            return
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is None or ctx.terminal:
            return
        stake = _arg("stake_amount", 1, args, kwargs)
        min_stake = _arg("min_stake_amount", 2, args, kwargs)
        max_stake = _arg("max_stake_amount", 3, args, kwargs)
        trade_amount = _arg("trade_amount", 4, args, kwargs)
        available = self._guard("available_stake", wallets.get_available_stake_amount)
        if not stake or (isinstance(stake, (int, float)) and stake <= 0):
            code = RejectionCode.INSUFFICIENT_STAKE.value
        else:
            allowed = min(v for v in (max_stake, available) if v is not None) if (
                max_stake is not None or available is not None) else None
            if allowed is not None and trade_amount:
                allowed = min(allowed, (max_stake or allowed) - trade_amount)
            if min_stake is not None and allowed is not None and min_stake > allowed:
                code = RejectionCode.INSUFFICIENT_BALANCE.value
            elif min_stake is not None and stake * 1.3 < min_stake:
                code = RejectionCode.MIN_NOTIONAL.value
            else:
                code = RejectionCode.INSUFFICIENT_STAKE.value
        self._reject(ctx, code, RejectionStage.BOT.value, {
            "stake_amount": stake, "min_stake_amount": min_stake, "max_stake_amount": max_stake,
            "available_stake": available, "trade_amount": trade_amount,
        })

    # -- the bot acts ----------------------------------------------------------
    def before_execute_entry(self, bot, *args, **kwargs) -> None:
        self.bot = bot
        pair = _arg("pair", 0, args, kwargs)
        stake = _arg("stake_amount", 1, args, kwargs)
        price = _arg("price", 2, args, kwargs)
        mode = kwargs.get("mode", "initial")
        trade = kwargs.get("trade")
        is_short = bool(kwargs.get("is_short", False))
        tag = _text(kwargs.get("enter_tag"))
        intent = "enter_short" if is_short else "enter_long"
        ctx = self.recorder.context(pair)

        if trade is not None and mode == "pos_adjust":
            self._ensure_trade_known(trade)
            row = self._row_for(pair)
            opened = self.recorder.open_decision(
                kind="add", intent=intent, symbol=pair, timeframe=self._timeframe(),
                candle=snapshots.candle_open(row), row=row, portfolio=self._portfolio,
                risk=lambda: self._risk(pair), entry_tag=tag, seq=int(trade.id),
                ft_trade_id=int(trade.id), trigger="adjust_trade_position",
            )
            if opened is None:
                return
            ctx = self.recorder.begin(pair, opened.decision_id, "add", position_id=opened.position_id,
                                      ft_trade_id=int(trade.id))
            self.recorder.adjust_decisions[int(trade.id)] = opened.decision_id
            self.recorder.trades.setdefault(int(trade.id), {})["adjusted"] = True
        elif trade is not None:
            # An order being re-placed (mode "replace") belongs to the decision that opened it.
            self._ensure_trade_known(trade)
            decision_id, position_id = self.recorder.decision_for_order(None, int(trade.id), True)
            if decision_id is None:
                return
            ctx = self.recorder.begin(pair, decision_id, "entry", position_id=position_id,
                                      ft_trade_id=int(trade.id))
        elif ctx is None:
            # No signal hook ran (a custom entry path): record the decision here, honestly labelled.
            row = self._row_for(pair)
            opened = self.recorder.open_decision(
                kind="entry", intent=intent, symbol=pair, timeframe=self._timeframe(),
                candle=snapshots.candle_open(row), row=row, portfolio=self._portfolio,
                risk=lambda: self._risk(pair), entry_tag=tag, derivation="execute_entry",
            )
            if opened is None:
                return
            ctx = self.recorder.begin(pair, opened.decision_id, "entry", position_id=opened.position_id)

        self.recorder.record_event(
            ctx.decision_id, EventType.BOT_INSTRUCTION_CREATED, discriminator=str(mode),
            payload={"pair": pair, "stake_amount": stake, "price": price, "mode": mode,
                     "is_short": is_short, "order_type": kwargs.get("ordertype"), "entry_tag": tag},
            ft_trade_id=ctx.ft_trade_id, event_source="freqtrade_callback",
        )

    def after_execute_entry(self, bot, result, *args, **kwargs) -> None:
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is None:
            return
        try:
            if result:
                self.recorder.mark_terminal(ctx.decision_id, "executed")
                ctx.terminal = True
            elif not ctx.terminal:
                if ctx.exchange_status in ("expired", "rejected"):
                    self._reject(ctx, RejectionCode.EXCHANGE_REJECTED.value, RejectionStage.EXCHANGE.value,
                                 {"order_status": ctx.exchange_status})
                else:
                    self._reject(ctx, RejectionCode.UNKNOWN.value, RejectionStage.BOT.value, {
                        "note": "execute_entry returned False before placing an order "
                                "(the strategy's confirm_trade_entry said no, a similar order "
                                "was already open, or the stake came to nothing)",
                        "mode": kwargs.get("mode", "initial"),
                    })
        finally:
            self.recorder.end(pair)

    def on_error_execute_entry(self, bot, exc, *args, **kwargs) -> None:
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is None:
            return
        try:
            if not ctx.terminal:
                code, stage = self.classify(exc)
                self._reject(ctx, code, stage, {"error": type(exc).__name__, "message": str(exc)[:300]})
        finally:
            self.recorder.end(pair)

    def after_create_trade(self, bot, result, *args, **kwargs) -> None:
        self.bot = bot
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is None:
            return
        try:
            if not result and not ctx.terminal:
                self._reject(ctx, RejectionCode.UNKNOWN.value, RejectionStage.BOT.value, {
                    "note": "create_trade returned False after the signal without saying why "
                            "(a depth-of-market check, or no free slot left this pass)",
                })
        finally:
            self.recorder.end(pair)

    def on_error_create_trade(self, bot, exc, *args, **kwargs) -> None:
        pair = _arg("pair", 0, args, kwargs)
        ctx = self.recorder.context(pair)
        if ctx is not None:
            if not ctx.terminal:
                code, stage = self.classify(exc)
                self._reject(ctx, code, stage, {"error": type(exc).__name__, "message": str(exc)[:300]})
            self.recorder.end(pair)

    def before_execute_trade_exit(self, bot, *args, **kwargs) -> None:
        self.bot = bot
        trade = _arg("trade", 0, args, kwargs)
        limit = _arg("limit", 1, args, kwargs)
        exit_check = _arg("exit_check", 2, args, kwargs)
        sub_trade_amt = kwargs.get("sub_trade_amt")
        exit_type = getattr(exit_check, "exit_type", None)
        exit_type = str(getattr(exit_type, "value", exit_type) or "")
        exit_reason = _text(kwargs.get("exit_tag")) or _text(getattr(exit_check, "exit_reason", None)) or exit_type
        kind = "reduce" if sub_trade_amt else "exit"
        intent = "exit_short" if getattr(trade, "is_short", False) else "exit_long"
        self._ensure_trade_known(trade)
        row = self._row_for(trade.pair)
        opened = self.recorder.open_decision(
            kind=kind, intent=intent, symbol=trade.pair, timeframe=self._timeframe(),
            candle=snapshots.candle_open(row), row=row, portfolio=self._portfolio,
            risk=lambda: self._risk(trade.pair), exit_reason=exit_reason, seq=int(trade.id),
            ft_trade_id=int(trade.id), trigger=exit_type or "exit",
        )
        if opened is None:
            return
        ctx = self.recorder.begin(trade.pair, opened.decision_id, kind, position_id=opened.position_id,
                                  ft_trade_id=int(trade.id))
        self.recorder.exit_decisions[int(trade.id)] = opened.decision_id
        self.recorder.record_event(
            ctx.decision_id, EventType.BOT_INSTRUCTION_CREATED, discriminator=f"{kind}:{exit_reason}",
            payload={"pair": trade.pair, "limit": limit, "exit_reason": exit_reason, "exit_type": exit_type,
                     "sub_trade_amount": sub_trade_amt, "order_type": kwargs.get("ordertype"),
                     "amount": getattr(trade, "amount", None), "open_rate": getattr(trade, "open_rate", None)},
            ft_trade_id=int(trade.id), event_source="freqtrade_callback",
        )

    def after_execute_trade_exit(self, bot, result, *args, **kwargs) -> None:
        trade = _arg("trade", 0, args, kwargs)
        ctx = self.recorder.context(trade.pair)
        if ctx is None:
            return
        try:
            if result:
                self.recorder.mark_terminal(ctx.decision_id, "executed")
                ctx.terminal = True
            elif not ctx.terminal:
                self._reject(ctx, RejectionCode.UNKNOWN.value, RejectionStage.BOT.value, {
                    "note": "execute_trade_exit returned False before placing an order "
                            "(the strategy's confirm_trade_exit said no, or a similar order was open)",
                })
        finally:
            self.recorder.end(trade.pair)

    def on_error_execute_trade_exit(self, bot, exc, *args, **kwargs) -> None:
        trade = _arg("trade", 0, args, kwargs)
        ctx = self.recorder.context(getattr(trade, "pair", None))
        if ctx is not None:
            if not ctx.terminal:
                code, stage = self.classify(exc)
                self._reject(ctx, code, stage, {"error": type(exc).__name__, "message": str(exc)[:300]})
            self.recorder.end(trade.pair)

    # -- the exchange answers ----------------------------------------------------
    def _order_fields(self, kwargs: dict) -> dict:
        return {key: kwargs.get(key) for key in ("pair", "ordertype", "side", "amount", "rate",
                                                  "leverage", "time_in_force", "reduceOnly",
                                                  "initial_order")}

    def before_create_order(self, exchange, *args, **kwargs) -> None:
        fields = self._order_fields(kwargs)
        ctx = self.recorder.context(fields["pair"]) if fields["pair"] else None
        self.recorder.record_event(
            ctx.decision_id if ctx else None, EventType.ORDER_SUBMITTED, symbol=fields["pair"],
            discriminator=f"{fields['side']}:{fields['ordertype']}:{fields['amount']}:{fields['rate']}",
            payload=fields, position_id=ctx.position_id if ctx else None,
            ft_trade_id=ctx.ft_trade_id if ctx else None, event_source="freqtrade_callback",
        )

    def after_create_order(self, exchange, result, *args, **kwargs) -> None:
        fields = self._order_fields(kwargs)
        ctx = self.recorder.context(fields["pair"]) if fields["pair"] else None
        order = result if isinstance(result, dict) else {}
        order_id = str(order.get("id")) if order.get("id") is not None else None
        status = order.get("status")
        self.recorder.record_event(
            ctx.decision_id if ctx else None, EventType.ORDER_ACKNOWLEDGED, symbol=fields["pair"],
            order_ref=order_id or "", discriminator=str(status), exchange_order_id=order_id,
            payload={"exchange_order_id": order_id, "status": status,
                     **{k: order.get(k) for k in ("type", "side", "amount", "filled", "remaining",
                                                  "average", "price", "cost", "datetime")}},
            position_id=ctx.position_id if ctx else None, ft_trade_id=ctx.ft_trade_id if ctx else None,
            event_source="freqtrade_callback",
        )
        if ctx is not None and order_id:
            ctx.order_ids.append(order_id)
            self.recorder.link_order(order_id, decision_id=ctx.decision_id, position_id=ctx.position_id,
                                     ft_trade_id=ctx.ft_trade_id, side=fields["side"])
            if status in ("expired", "rejected") and not order.get("filled"):
                ctx.exchange_status = str(status)

    def on_error_create_order(self, exchange, exc, *args, **kwargs) -> None:
        fields = self._order_fields(kwargs)
        ctx = self.recorder.context(fields["pair"]) if fields["pair"] else None
        code, stage = self.classify(exc)
        self.recorder.record_event(
            ctx.decision_id if ctx else None, EventType.ORDER_REJECTED, symbol=fields["pair"],
            discriminator=f"{code}:{type(exc).__name__}", rejection_stage=stage, rejection_code=code,
            payload={"error": type(exc).__name__, "exchange_message": str(exc)[:500], **fields},
            position_id=ctx.position_id if ctx else None, ft_trade_id=ctx.ft_trade_id if ctx else None,
            event_source="freqtrade_callback",
        )
        if ctx is not None:
            ctx.terminal = True
            self.recorder.mark_terminal(ctx.decision_id, f"rejected:{code}")

    # -- fills, through the strategy callback ------------------------------------
    def after_load_strategy(self, strategy, config: dict) -> None:
        self.strategy = strategy
        self.config = dict(config or {})
        built = provenance_module.build_provenance(
            strategy_name=str(config.get("strategy") or type(strategy).__name__),
            strategy=strategy, config=config, freqtrade_version=self.version,
        )
        self.recorder.set_provenance(**{k: v for k, v in built.items() if k != "environment"})
        self.install_order_filled(strategy)

    def install_order_filled(self, strategy: Any) -> None:
        original = getattr(strategy, "order_filled", None)
        if original is None or getattr(original, "__learning_wrapped__", None) is not None:
            return
        adapter = self

        def order_filled(pair, trade, order, current_time, **kwargs):
            result = original(pair=pair, trade=trade, order=order, current_time=current_time, **kwargs)
            adapter._guard("order_filled", adapter.on_order_filled, pair, trade, order, current_time)
            return result

        # freqtrade deep-copies `trade` for callbacks a strategy wrote and skips
        # the copy for the base class's own. Keep whichever behaviour applied.
        order_filled.__qualname__ = getattr(original, "__qualname__", "order_filled")
        order_filled.__name__ = "order_filled"
        order_filled.__learning_wrapped__ = original  # type: ignore[attr-defined]
        strategy.order_filled = order_filled
        self.patched.append("strategy.order_filled")

    def on_order_filled(self, pair: str, trade: Any, order: Any, current_time: datetime | None) -> None:
        self._ensure_trade_known(trade)
        order_id = getattr(order, "order_id", None)
        is_entry = getattr(order, "ft_order_side", None) == getattr(trade, "entry_side", "buy")
        decision_id, position_id = self.recorder.decision_for_order(order_id, int(trade.id), is_entry)
        status = getattr(order, "status", None)
        filled = float(getattr(order, "safe_filled", 0) or 0)
        payload = {
            "status": status, "side": getattr(order, "ft_order_side", None),
            "order_type": getattr(order, "order_type", None), "amount": getattr(order, "safe_amount", None),
            "filled": filled, "remaining": getattr(order, "safe_remaining", None),
            "average": getattr(order, "safe_price", None), "cost": getattr(order, "cost", None),
            "order_tag": getattr(order, "ft_order_tag", None),
            "filled_at": getattr(order, "order_filled_utc", None),
        }
        common = dict(event_time=current_time, order_ref=str(order_id or ""), exchange_order_id=order_id,
                      ft_order_id=getattr(order, "id", None), ft_trade_id=int(trade.id), symbol=pair,
                      position_id=position_id, payload=payload, event_source="freqtrade_callback")
        if status == "closed":
            self.recorder.record_event(decision_id, EventType.FILL_COMPLETED, discriminator="closed", **common)
        elif filled > 0:
            self.recorder.record_event(decision_id, EventType.PARTIAL_FILL,
                                       discriminator=f"{status}:{filled}", **common)

    # -- the trade rows: linking, and the positions --------------------------------
    def _ensure_trade_known(self, trade: Any) -> dict | None:
        trade_id = getattr(trade, "id", None)
        if trade_id is None:
            return None
        known = self.recorder.trade(int(trade_id))
        if known is not None and known.get("origin_decision_id"):
            return known
        decision = self._guard("custom_data.read", self._read_custom, trade, CUSTOM_DATA_DECISION)
        position = self._guard("custom_data.read", self._read_custom, trade, CUSTOM_DATA_POSITION)
        entry = self.recorder.link_trade(int(trade_id), trade.pair, position_id=position,
                                         origin_decision_id=decision)
        if not decision:
            self.recorder.record_event(
                None, EventType.UNLINKED_POSITION_OBSERVED, key_time=getattr(trade, "open_date_utc", None),
                order_ref=str(trade_id), symbol=trade.pair, position_id=entry.get("position_id"),
                ft_trade_id=int(trade_id), event_source="learning_adapter",
                payload={"note": "a position this module did not see opened; no decision is invented",
                         "open_date": getattr(trade, "open_date_utc", None),
                         "amount": getattr(trade, "amount", None),
                         "open_rate": getattr(trade, "open_rate", None),
                         "stake_amount": getattr(trade, "stake_amount", None),
                         "enter_tag": getattr(trade, "enter_tag", None),
                         "strategy": getattr(trade, "strategy", None)},
            )
        return entry

    @staticmethod
    def _read_custom(trade: Any, key: str) -> Any:
        reader = getattr(trade, "get_custom_data", None)
        return reader(key) if reader is not None else None

    def _persist_correlation(self, trade_id: int, entry: dict) -> None:
        try:
            self.custom_data.set_custom_data(int(trade_id), CUSTOM_DATA_DECISION, entry.get("origin_decision_id"))
            self.custom_data.set_custom_data(int(trade_id), CUSTOM_DATA_POSITION, entry.get("position_id"))
            self.recorder.pending_custom_data.pop(int(trade_id), None)
        except Exception as exc:  # noqa: BLE001 - retried on the next message for this trade
            self.recorder.pending_custom_data[int(trade_id)] = entry
            self.recorder.note_error("custom_data.write", exc)

    def _retry_pending_correlations(self) -> None:
        for trade_id, entry in list(self.recorder.pending_custom_data.items()):
            self._persist_correlation(trade_id, entry)

    def after_startup(self, bot, result) -> None:
        self.bot = bot
        for trade in self.Trade.get_open_trades():
            self._guard("startup.link", self._ensure_trade_known, trade)

    def after_process(self, bot, result) -> None:
        self.bot = bot
        state = getattr(bot, "state", None)
        name = str(getattr(state, "name", state) or "").upper()
        if name not in ("RUNNING", "PAUSED"):
            return
        self._scan_whitelist(bot, paused=(name == "PAUSED"))
        if self.recorder.pending_custom_data:
            self._retry_pending_correlations()

    def _scan_whitelist(self, bot, *, paused: bool) -> None:
        """Signals on the current candle the bot never brought to create_trade, and why."""
        pairs = list(getattr(bot, "active_pair_whitelist", None) or [])
        if not pairs:
            return
        timeframe = self._timeframe()
        now = self.recorder.now()
        try:
            open_pairs = {trade.pair: int(trade.id) for trade in self.Trade.get_open_trades()}
        except Exception as exc:  # noqa: BLE001 - no honest attribution without it
            self.recorder.note_error("scan.open_trades", exc)
            return
        free = self._guard("scan.free_slots", bot.get_free_open_trades)
        global_lock = bool(self._guard("scan.global_lock", self.PairLocks.is_global_lock, side="*"))
        for pair in pairs:
            row = self._row_for(pair)
            intent = snapshots.wants_entry(row)
            if not intent:
                continue
            opened_at = snapshots.candle_open(row)
            if opened_at is None or snapshots.is_outdated(opened_at, timeframe, now):
                continue
            if self.recorder.has_decision(kind="entry", intent=intent, symbol=pair,
                                          timeframe=timeframe, candle=opened_at):
                continue
            if paused:
                code, detail = RejectionCode.BOT_PAUSED.value, {}
            elif pair in open_pairs:
                code, detail = RejectionCode.POSITION_ALREADY_OPEN.value, {"ft_trade_id": open_pairs[pair]}
            elif global_lock:
                lock = self._guard("scan.lock", self.PairLocks.get_pair_longest_lock, "*")
                code, detail = RejectionCode.GLOBAL_PAIRLOCK.value, {"lock": snapshots.lock_info(lock) if lock else None}
            elif free is not None and free <= 0:
                code, detail = RejectionCode.MAX_OPEN_TRADES.value, {
                    "max_open_trades": self.config.get("max_open_trades"), "open_positions": len(open_pairs)}
            else:
                continue  # create_trade saw it, or will on the next pass
            opened = self.recorder.open_decision(
                kind="entry", intent=intent, symbol=pair, timeframe=timeframe, candle=opened_at, row=row,
                portfolio=self._portfolio, risk=lambda p=pair: self._risk(p),
                entry_tag=_text(row.get("enter_tag")) if row else None, derivation="whitelist_scan",
            )
            if opened is None or not opened.new:
                continue
            self.recorder.record_event(
                opened.decision_id, EventType.SIGNAL_GENERATED, key_time=opened_at,
                payload={"intent": intent, "entry_tag": _text(row.get("enter_tag")) if row else None,
                         "candle_open": opened_at, "derived": True,
                         "note": "seen by the learning adapter on the analysed candle; freqtrade did "
                                 "not bring this pair to create_trade this candle"},
                event_source="learning_adapter",
            )
            self.recorder.reject(opened.decision_id, code, RejectionStage.BOT.value, payload=detail)

    # -- what freqtrade announces --------------------------------------------------
    def after_rpc_manager_init(self, manager, result, *args, **kwargs) -> None:
        freqtrade = _arg("freqtrade", 0, args, kwargs)
        config = getattr(freqtrade, "config", None) or {}
        handler = LearningRPCHandler(getattr(manager, "_rpc", None), config, self, base=self.rpc_handler_base)
        manager.registered_modules.append(handler)
        self.patched.append("RPCManager.registered_modules")

    def on_rpc(self, msg: dict) -> None:
        kind = str(getattr(msg.get("type"), "value", msg.get("type")) or "")
        if kind == "entry":
            self._on_entry(msg)
        elif kind == "entry_fill":
            self._on_entry_fill(msg)
        elif kind == "exit":
            self._on_exit(msg)
        elif kind == "exit_fill":
            self._on_exit_fill(msg)
        elif kind in ("entry_cancel", "exit_cancel"):
            self._on_cancel(msg, kind.split("_")[0])
        elif kind in ("protection_trigger", "protection_trigger_global"):
            self._on_protection(msg, kind)
        elif kind in ("status", "startup", "warning", "exception"):
            self._on_status(msg, kind)

    def _on_entry(self, msg: dict) -> None:
        trade_id = int(msg["trade_id"])
        pair = msg.get("pair")
        ctx = self.recorder.context(pair)
        if ctx is None:
            return
        ctx.ft_trade_id = trade_id
        for order_id in ctx.order_ids:
            link = self.recorder.order_link(order_id)
            if link is not None:
                link["ft_trade_id"] = trade_id
        if ctx.kind == "add" or msg.get("sub_trade"):
            entry = self.recorder.link_trade(trade_id, pair, position_id=ctx.position_id, origin_decision_id=None)
            self.recorder.adjust_decisions[trade_id] = ctx.decision_id
            entry["adjusted"] = True
            return
        entry = self.recorder.link_trade(trade_id, pair, position_id=ctx.position_id,
                                         origin_decision_id=ctx.decision_id)
        self._persist_correlation(trade_id, entry)

    def _on_entry_fill(self, msg: dict) -> None:
        trade_id = int(msg["trade_id"])
        sub_trade = bool(msg.get("sub_trade"))
        decision_id, position_id = self.recorder.decision_for_order(None, trade_id, True)
        if sub_trade:
            decision_id = self.recorder.adjust_decisions.get(trade_id, decision_id)
        self.recorder.record_event(
            decision_id, EventType.POSITION_ADJUSTED if sub_trade else EventType.POSITION_OPENED,
            order_ref=str(trade_id), discriminator=f"entry:{msg.get('amount')}",
            payload={k: msg.get(k) for k in ("pair", "direction", "amount", "open_rate", "stake_amount",
                                             "enter_tag", "open_date", "order_type", "sub_trade")},
            ft_trade_id=trade_id, symbol=msg.get("pair"), position_id=position_id, event_source="freqtrade_rpc",
        )

    def _on_exit(self, msg: dict) -> None:
        trade_id = int(msg["trade_id"])
        ctx = self.recorder.context(msg.get("pair"))
        if ctx is not None and ctx.kind in ("exit", "reduce"):
            ctx.ft_trade_id = trade_id
            self.recorder.exit_decisions[trade_id] = ctx.decision_id

    def _on_exit_fill(self, msg: dict) -> None:
        trade_id = int(msg["trade_id"])
        final = bool(msg.get("is_final_exit", not msg.get("sub_trade")))
        decision_id = self.recorder.exit_decisions.get(trade_id)
        entry = self.recorder.trade(trade_id) or {}
        self.recorder.record_event(
            decision_id, EventType.POSITION_CLOSED if final else EventType.POSITION_ADJUSTED,
            order_ref=str(trade_id), discriminator=f"exit:{msg.get('amount')}",
            payload={k: msg.get(k) for k in ("pair", "direction", "amount", "open_rate", "close_rate",
                                             "profit_amount", "profit_ratio", "cumulative_profit",
                                             "final_profit_ratio", "exit_reason", "open_date", "close_date",
                                             "order_type", "sub_trade", "is_final_exit")},
            ft_trade_id=trade_id, symbol=msg.get("pair"), position_id=entry.get("position_id"),
            event_source="freqtrade_rpc",
        )
        if final:
            self.recorder.close_trade(trade_id)

    def _on_cancel(self, msg: dict, side: str) -> None:
        trade_id = int(msg["trade_id"])
        reason = str(msg.get("reason") or "")
        if side == "entry":
            decision_id, position_id = self.recorder.decision_for_order(None, trade_id, True)
        else:
            decision_id = self.recorder.exit_decisions.get(trade_id)
            position_id = (self.recorder.trade(trade_id) or {}).get("position_id")
        code = RejectionCode.ORDER_TIMEOUT.value if "timeout" in reason.lower() else RejectionCode.ORDER_CANCELLED.value
        self.recorder.record_event(
            decision_id, EventType.ORDER_CANCELLED, order_ref=str(trade_id), discriminator=f"{side}:{reason}",
            rejection_stage=RejectionStage.EXECUTION.value, rejection_code=code,
            payload={"side": side, "reason": reason,
                     **{k: msg.get(k) for k in ("pair", "order_type", "amount", "order_rate", "sub_trade")}},
            ft_trade_id=trade_id, symbol=msg.get("pair"), position_id=position_id, event_source="freqtrade_rpc",
        )

    def _on_protection(self, msg: dict, kind: str) -> None:
        pair = msg.get("pair")
        self.recorder.record_event(
            None, EventType.RISK_DECISION, symbol=pair, order_ref=str(pair or "*"),
            discriminator=f"{kind}:{msg.get('reason')}:{msg.get('lock_end_time')}",
            payload={"kind": kind, **{k: msg.get(k) for k in ("pair", "side", "reason", "lock_time",
                                                                "lock_end_time", "base_currency")}},
            event_source="freqtrade_rpc",
        )

    def _on_status(self, msg: dict, kind: str) -> None:
        status = str(msg.get("status") or "")
        self.recorder.record_event(
            None, EventType.BOT_STATUS, discriminator=f"{kind}:{status[:80]}",
            payload={"type": kind, "status": status[:1000]}, event_source="freqtrade_rpc",
        )


class LearningRPCHandler:
    """A freqtrade RPC module that listens and never speaks."""

    def __new__(cls, rpc, config, adapter, base=object):
        # Subclass freqtrade's RPCHandler when it is available, so RPCManager
        # sees exactly the shape it expects (name, _config, send_msg, cleanup).
        if base is not object and not issubclass(cls, base):
            cls = type("LearningRPCHandler", (base, cls), {})
        return super(LearningRPCHandler, cls).__new__(cls)

    def __init__(self, rpc, config, adapter, base=object) -> None:
        self._rpc = rpc
        self._config = config
        self._adapter = adapter

    @property
    def name(self) -> str:
        return "learning"

    def cleanup(self) -> None:
        self._adapter._guard("rpc.cleanup", self._adapter.on_cleanup)

    def send_msg(self, msg: dict) -> None:
        self._adapter._guard("rpc.send_msg", self._adapter.on_rpc, msg)


def install(recorder: Recorder, *, bot_name: str, stake_currency: str,
            on_cleanup: Callable[[], Any] | None = None, log: Callable[[str], None] = print) -> FreqtradeAdapter:
    """Import freqtrade and hook it. Raises only when freqtrade itself cannot be imported."""
    import freqtrade
    from freqtrade import exceptions
    from freqtrade.exchange import Exchange
    from freqtrade.freqtradebot import FreqtradeBot
    from freqtrade.persistence import PairLocks, Trade
    from freqtrade.persistence.custom_data import CustomDataWrapper
    from freqtrade.resolvers import StrategyResolver
    from freqtrade.rpc import RPCHandler
    from freqtrade.rpc.rpc_manager import RPCManager
    from freqtrade.strategy.interface import IStrategy
    from freqtrade.wallets import Wallets

    adapter = FreqtradeAdapter(
        recorder, Trade=Trade, PairLocks=PairLocks, custom_data=CustomDataWrapper, exceptions=exceptions,
        rpc_handler_base=RPCHandler, version=getattr(freqtrade, "__version__", None), bot_name=bot_name,
        stake_currency=stake_currency, on_cleanup=on_cleanup, log=log,
    )
    adapter.patch(FreqtradeBot=FreqtradeBot, Wallets=Wallets, Exchange=Exchange, IStrategy=IStrategy,
                  RPCManager=RPCManager, StrategyResolver=StrategyResolver)
    return adapter
