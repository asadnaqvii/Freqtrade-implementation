"""The recorder: opens decisions, attaches events, remembers what belongs to what.

This is the stateful heart of the adapter and it knows nothing about
freqtrade: the adapter translates freqtrade's objects into plain arguments
and the recorder turns those into records in the outbox. That split is what
makes the behaviour testable without a bot, and it is also where the three
guarantees live:

  One decision per candle. A decision is keyed on the candle it was made on,
  so the same signal noticed on every five-second pass of a four-hour candle
  is one record; the repeats are counted, not stored.

  Never invent, never lose. A record that fails its own point-in-time check
  is written with quarantined=true rather than dropped; a repeated rejection
  is a metric; a trade the module did not see opened gets no made-up decision.

  Never raise into the trading loop. Every public method swallows its own
  failures, counts them, and returns something harmless.
"""

from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from app.learning import snapshots
from app.learning.canonical import sanitise
from app.learning.contracts import LeakageError, TradingDecision, TradingEvent
from app.learning.enums import EventType, RejectionStage
from app.learning.ids import new_decision_id, new_event_id, new_position_id
from app.learning.keys import decision_key, event_key, time_bucket

#: How many decision keys to remember for dedupe. A 25-pair whitelist on 4h
#: candles produces well under a hundred a day; this is weeks.
REMEMBERED_DECISIONS = 5000

#: Log the first few adapter errors in full; after that, count them.
LOGGED_ERRORS = 20


def _plain(value: Any) -> str:
    """An enum member as its value; anything else as text."""
    return str(getattr(value, "value", value))


@dataclass
class Opened:
    decision_id: str
    key: str
    new: bool
    position_id: str | None = None


@dataclass
class Context:
    """One in-flight action on a pair: the decision the next order belongs to."""
    pair: str
    decision_id: str
    kind: str
    position_id: str | None = None
    ft_trade_id: int | None = None
    order_ids: list = field(default_factory=list)
    exchange_status: str | None = None
    terminal: bool = False


class Recorder:
    def __init__(self, outbox: Any, identity: dict, *, environment: str, exchange: str,
                 strategy_id: str, provenance: dict | None = None,
                 clock: Callable[[], datetime] | None = None,
                 log: Callable[[str], None] | None = None) -> None:
        self.outbox = outbox
        self.identity = identity
        self.environment = environment
        self.exchange = exchange
        self.strategy_id = strategy_id
        self.provenance: dict = dict(provenance or {})
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._log = log or (lambda message: print(message, flush=True))
        self._lock = threading.RLock()
        self._decisions: OrderedDict[str, str] = OrderedDict()
        self._info: dict[str, dict] = {}
        self._rejections: dict[tuple[str, str], int] = {}
        self._contexts: dict[str, Context] = {}
        self.trades: dict[int, dict] = {}
        self.orders: dict[str, dict] = {}
        self.exit_decisions: dict[int, str] = {}
        self.adjust_decisions: dict[int, str] = {}
        self.pending_custom_data: dict[int, dict] = {}
        self.stats: dict[str, Any] = {
            "decisions_opened": 0, "decisions_deduped": 0, "decisions_quarantined": 0,
            "events_recorded": 0, "events_deduped": 0, "rejections": {},
            "adapter_errors": 0, "last_error": None,
        }

    # -- plumbing ------------------------------------------------------------
    def now(self) -> datetime:
        return self._clock()

    def note_error(self, where: str, exc: BaseException) -> None:
        """An adapter hook failed. Count it; trading has already moved on."""
        with self._lock:
            self.stats["adapter_errors"] += 1
            self.stats["last_error"] = f"{where}: {str(exc)[:200]}"
            if self.stats["adapter_errors"] <= LOGGED_ERRORS:
                self._log(f"learning: {where} failed ({type(exc).__name__}: {str(exc)[:200]})")

    def set_provenance(self, **fields: Any) -> None:
        with self._lock:
            self.provenance.update({k: v for k, v in fields.items() if v is not None})

    def _bot_key(self) -> str:
        # The key must not change when registration finishes: use the name,
        # which is fixed at boot, rather than the instance id, which arrives later.
        return str(self.identity.get("bot_name") or self.identity.get("bot_instance_id") or "")

    # -- decisions -----------------------------------------------------------
    def decision_key_for(self, *, kind: str, intent: str, symbol: str, timeframe: str,
                         candle: datetime, seq: int = 0) -> str:
        return decision_key(
            bot_instance_id=self._bot_key(), strategy_id=self.strategy_id,
            strategy_code_hash=self.provenance.get("strategy_code_hash"), symbol=symbol,
            timeframe=timeframe, decision_kind=kind, strategy_intent=intent,
            candle_open=time_bucket(candle, 1), seq=seq,
        )

    def has_decision(self, **key_fields: Any) -> str | None:
        with self._lock:
            return self._decisions.get(self.decision_key_for(**key_fields))

    def info(self, decision_id: str) -> dict:
        return self._info.get(decision_id, {})

    def open_decision(self, *, kind: str, intent: str, symbol: str, timeframe: str,
                      candle: datetime | None, row: dict | None = None,
                      portfolio: Callable[[], dict] | None = None,
                      risk: Callable[[], dict] | None = None,
                      entry_tag: str | None = None, exit_reason: str | None = None,
                      seq: int = 0, position_id: str | None = None,
                      ft_trade_id: int | None = None, trigger: str | None = None,
                      derivation: str | None = None) -> Opened | None:
        """One decision per (candle, kind, intent, symbol, seq). Never raises."""
        try:
            return self._open(kind=kind, intent=intent, symbol=symbol, timeframe=timeframe,
                              candle=candle, row=row, portfolio=portfolio, risk=risk,
                              entry_tag=entry_tag, exit_reason=exit_reason, seq=seq,
                              position_id=position_id, ft_trade_id=ft_trade_id,
                              trigger=trigger, derivation=derivation)
        except Exception as exc:  # noqa: BLE001
            self.note_error("open_decision", exc)
            return None

    def _open(self, *, kind, intent, symbol, timeframe, candle, row, portfolio, risk, entry_tag,
              exit_reason, seq, position_id, ft_trade_id, trigger, derivation) -> Opened:
        now = self.now()
        opened_at = candle or snapshots.floor_to_candle(now, timeframe)
        key = self.decision_key_for(kind=kind, intent=intent, symbol=symbol, timeframe=timeframe,
                                    candle=opened_at, seq=seq)
        with self._lock:
            existing = self._decisions.get(key)
            if existing:
                self.stats["decisions_deduped"] += 1
                return Opened(existing, key, False, self._info[existing].get("position_id"))

            decision_id = new_decision_id()
            if position_id is None:
                if ft_trade_id is not None and ft_trade_id in self.trades:
                    position_id = self.trades[ft_trade_id].get("position_id")
                elif kind == "entry":
                    position_id = new_position_id()
                elif ft_trade_id is not None:
                    position_id = self.fallback_position_id(ft_trade_id)

            market, features, signals = snapshots.split_row(row, timeframe)
            provenance = dict(self.provenance)
            provenance.update({"trigger": trigger, "derivation": derivation,
                               "signals": signals or None})
            provenance = {k: v for k, v in provenance.items() if v is not None}
            close = snapshots.candle_close(candle, timeframe) if candle else None

            record = TradingDecision(
                decision_id=decision_id,
                decision_time_utc=now.isoformat(),
                environment=self.environment,
                exchange=self.exchange,
                symbol=symbol,
                timeframe=timeframe,
                decision_kind=kind,
                strategy_intent=intent,
                strategy_id=self.strategy_id,
                strategy_version=provenance.get("strategy_version"),
                strategy_code_hash=provenance.get("strategy_code_hash"),
                parameter_hash=provenance.get("parameter_hash"),
                runtime_config_hash=provenance.get("runtime_config_hash"),
                feature_set_version=str(provenance.get("feature_set_version") or "unversioned"),
                idempotency_key=key,
                owner_id=self.identity.get("owner_id"),
                bot_instance_id=self.identity.get("bot_instance_id"),
                account_id=self.identity.get("account_id"),
                run_id=provenance.get("run_id"),
                position_id=position_id,
                entry_tag=entry_tag,
                exit_reason=exit_reason,
                market_data_max_ts=close.isoformat() if close else None,
                market_context=market,
                feature_snapshot=features,
                portfolio_snapshot=self._snapshot("portfolio", portfolio),
                risk_snapshot=self._snapshot("risk", risk),
                provenance=provenance,
            )
            quarantine = None
            try:
                record.validate()
            except LeakageError as exc:
                quarantine = f"future_market_data: {exc}"
            if quarantine:
                record = TradingDecision(**{**record.__dict__, "quarantined": True,
                                            "quarantine_reason": quarantine[:500]})
                self.stats["decisions_quarantined"] += 1

            self.outbox.enqueue("decision", key, record.to_row())
            self._decisions[key] = decision_id
            while len(self._decisions) > REMEMBERED_DECISIONS:
                _, gone = self._decisions.popitem(last=False)
                self._info.pop(gone, None)
            self._info[decision_id] = {
                "kind": kind, "intent": intent, "symbol": symbol, "timeframe": timeframe,
                "candle": opened_at, "position_id": position_id, "ft_trade_id": ft_trade_id,
                "terminal": None,
            }
            self.stats["decisions_opened"] += 1
            return Opened(decision_id, key, True, position_id)

    def _snapshot(self, label: str, build: Callable[[], dict] | None) -> dict:
        if build is None:
            return {}
        try:
            return sanitise(build()) or {}
        except Exception as exc:  # noqa: BLE001
            self.note_error(f"{label}_snapshot", exc)
            return {"_unavailable": str(exc)[:200]}

    def fallback_position_id(self, ft_trade_id: int) -> str:
        """For a position the module did not see opened: honest, and deterministic."""
        return f"ft:{self._bot_key()}:{int(ft_trade_id)}"

    def mark_terminal(self, decision_id: str, outcome: str) -> None:
        with self._lock:
            if decision_id in self._info and not self._info[decision_id].get("terminal"):
                self._info[decision_id]["terminal"] = outcome

    def is_terminal(self, decision_id: str) -> bool:
        return bool(self._info.get(decision_id, {}).get("terminal"))

    # -- events --------------------------------------------------------------
    def record_event(self, decision_id: str | None, event_type: str, *, event_time: datetime | None = None,
                     key_time: datetime | None = None, order_ref: str = "", discriminator: str = "",
                     payload: dict | None = None, symbol: str | None = None,
                     position_id: str | None = None, ft_trade_id: int | None = None,
                     ft_order_id: int | None = None, exchange_order_id: str | None = None,
                     rejection_stage: str | None = None, rejection_code: str | None = None,
                     event_source: str = "learning_adapter", bucket_seconds: int = 1) -> bool:
        """Attach one event. True when it was new. Never raises."""
        try:
            when = event_time or self.now()
            key = event_key(decision_id=decision_id, event_type=_plain(event_type),
                            event_time=key_time or when, order_ref=order_ref or "",
                            discriminator=discriminator or "", bucket_seconds=bucket_seconds)
            info = self._info.get(decision_id or "", {})
            record = TradingEvent(
                event_id=new_event_id(),
                event_type=_plain(event_type),
                event_time_utc=when.isoformat(),
                event_source=_plain(event_source),
                idempotency_key=key,
                decision_id=decision_id,
                owner_id=self.identity.get("owner_id"),
                bot_instance_id=self.identity.get("bot_instance_id"),
                symbol=symbol or info.get("symbol"),
                position_id=position_id or info.get("position_id"),
                ft_trade_id=ft_trade_id if ft_trade_id is not None else info.get("ft_trade_id"),
                ft_order_id=ft_order_id,
                exchange_order_id=exchange_order_id,
                rejection_stage=_plain(rejection_stage) if rejection_stage else None,
                rejection_code=_plain(rejection_code) if rejection_code else None,
                payload=sanitise(payload or {}),
            )
            record.validate()
            new = self.outbox.enqueue("event", key, record.to_row())
            with self._lock:
                self.stats["events_recorded" if new else "events_deduped"] += 1
            return bool(new)
        except Exception as exc:  # noqa: BLE001
            self.note_error(f"record_event:{event_type}", exc)
            return False

    def reject(self, decision_id: str | None, code: str, stage: str, *, payload: dict | None = None,
               **fields: Any) -> bool:
        """A signal was not acted on. One event per (decision, reason); repeats are counted."""
        if decision_id is None:
            return False
        with self._lock:
            seen = self._rejections.get((decision_id, code), 0)
            self._rejections[(decision_id, code)] = seen + 1
            per_code = self.stats["rejections"]
            per_code[code] = per_code.get(code, 0) + 1
        if seen:
            return False
        new = self.record_event(decision_id, EventType.SIGNAL_REJECTED, discriminator=code,
                                key_time=self._info.get(decision_id, {}).get("candle"),
                                rejection_stage=stage, rejection_code=code,
                                payload={"reason": code, **(payload or {})}, **fields)
        self.mark_terminal(decision_id, f"rejected:{code}")
        return new

    def repeat_count(self, decision_id: str, code: str) -> int:
        return self._rejections.get((decision_id, code), 0)

    # -- in-flight contexts --------------------------------------------------
    def begin(self, pair: str, decision_id: str, kind: str, *, position_id: str | None = None,
              ft_trade_id: int | None = None) -> Context:
        with self._lock:
            ctx = Context(pair=pair, decision_id=decision_id, kind=kind,
                          position_id=position_id or self._info.get(decision_id, {}).get("position_id"),
                          ft_trade_id=ft_trade_id)
            self._contexts[pair] = ctx
            return ctx

    def context(self, pair: str) -> Context | None:
        return self._contexts.get(pair)

    def end(self, pair: str) -> None:
        with self._lock:
            self._contexts.pop(pair, None)

    # -- correlation ---------------------------------------------------------
    def link_trade(self, ft_trade_id: int, pair: str, *, position_id: str | None,
                   origin_decision_id: str | None) -> dict:
        with self._lock:
            entry = self.trades.setdefault(int(ft_trade_id), {"pair": pair})
            entry["pair"] = pair
            if position_id:
                entry["position_id"] = position_id
            if origin_decision_id:
                entry["origin_decision_id"] = origin_decision_id
            entry.setdefault("position_id", self.fallback_position_id(ft_trade_id))
            try:
                self.outbox.remember_correlation(int(ft_trade_id), entry.get("position_id"),
                                                 entry.get("origin_decision_id"))
            except Exception:  # noqa: BLE001 - memory is the primary copy
                pass
            return entry

    def trade(self, ft_trade_id: int | None) -> dict | None:
        if ft_trade_id is None:
            return None
        return self.trades.get(int(ft_trade_id))

    def trade_for_pair(self, pair: str) -> int | None:
        with self._lock:
            for trade_id, entry in self.trades.items():
                if entry.get("pair") == pair and not entry.get("closed"):
                    return trade_id
        return None

    def close_trade(self, ft_trade_id: int) -> None:
        with self._lock:
            entry = self.trades.get(int(ft_trade_id))
            if entry is not None:
                entry["closed"] = True

    def link_order(self, exchange_order_id: str, *, decision_id: str, position_id: str | None,
                   ft_trade_id: int | None, side: str | None) -> None:
        if not exchange_order_id:
            return
        with self._lock:
            self.orders[str(exchange_order_id)] = {
                "decision_id": decision_id, "position_id": position_id,
                "ft_trade_id": ft_trade_id, "side": side,
            }
            while len(self.orders) > REMEMBERED_DECISIONS:
                self.orders.pop(next(iter(self.orders)))

    def order_link(self, exchange_order_id: str | None) -> dict | None:
        if not exchange_order_id:
            return None
        return self.orders.get(str(exchange_order_id))

    def decision_for_order(self, exchange_order_id: str | None, ft_trade_id: int | None,
                           is_entry_side: bool) -> tuple[str | None, str | None]:
        """(decision_id, position_id) for an order, by order id first, then by trade."""
        link = self.order_link(exchange_order_id)
        if link:
            return link.get("decision_id"), link.get("position_id")
        entry = self.trade(ft_trade_id)
        if entry is None:
            return None, None
        if is_entry_side:
            decision = self.adjust_decisions.get(int(ft_trade_id)) if entry.get("adjusted") else None
            return decision or entry.get("origin_decision_id"), entry.get("position_id")
        return self.exit_decisions.get(int(ft_trade_id)), entry.get("position_id")

    # -- observability -------------------------------------------------------
    def health(self) -> dict:
        with self._lock:
            return {
                **{k: (dict(v) if isinstance(v, dict) else v) for k, v in self.stats.items()},
                "open_contexts": len(self._contexts),
                "linked_trades": len([t for t in self.trades.values() if not t.get("closed")]),
                "remembered_decisions": len(self._decisions),
            }
