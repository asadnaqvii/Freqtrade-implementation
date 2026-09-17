"""The closed vocabularies of the Learning Module.

Strings on the wire, enums in the code: a record is only queryable if two
producers spell the same thing the same way, and an enum is how that is
enforced without a lookup table.
"""

from __future__ import annotations

from enum import Enum


class DecisionKind(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"
    ADD = "add"
    REDUCE = "reduce"


class StrategyIntent(str, Enum):
    ENTER_LONG = "enter_long"
    ENTER_SHORT = "enter_short"
    EXIT_LONG = "exit_long"
    EXIT_SHORT = "exit_short"


class EventType(str, Enum):
    # strategy
    SIGNAL_GENERATED = "signal_generated"
    SIGNAL_REJECTED = "signal_rejected"
    EXIT_SIGNAL_GENERATED = "exit_signal_generated"
    # decision / risk
    RISK_DECISION = "risk_decision"
    BOT_INSTRUCTION_CREATED = "bot_instruction_created"
    # execution
    EXECUTION_PLAN = "execution_plan"
    ORDER_SUBMITTED = "order_submitted"
    ORDER_ACKNOWLEDGED = "order_acknowledged"
    ORDER_REJECTED = "order_rejected"
    ORDER_CANCELLED = "order_cancelled"
    PARTIAL_FILL = "partial_fill"
    FILL_COMPLETED = "fill_completed"
    # position
    POSITION_OPENED = "position_opened"
    POSITION_ADJUSTED = "position_adjusted"
    POSITION_CLOSED = "position_closed"
    # verification, by reference only
    VERIFICATION_REFERENCE = "verification_reference"
    VERIFICATION_STATUS_CHANGED = "verification_status_changed"
    UNACCOUNTED_EXCHANGE_ACTIVITY = "unaccounted_exchange_activity"
    # operational: things that happened to the bot, not to a decision
    UNLINKED_POSITION_OBSERVED = "unlinked_position_observed"
    BOT_STATUS = "bot_status"


class EventSource(str, Enum):
    FREQTRADE_RPC = "freqtrade_rpc"
    FREQTRADE_CALLBACK = "freqtrade_callback"
    LEARNING_ADAPTER = "learning_adapter"
    VERIFIER = "verifier"


class RejectionStage(str, Enum):
    STRATEGY = "strategy"
    BOT = "bot"
    RISK = "risk"
    EXECUTION = "execution"
    EXCHANGE = "exchange"
    SYSTEM = "system"


class RejectionCode(str, Enum):
    MAX_OPEN_TRADES = "MAX_OPEN_TRADES"
    PAIR_LOCKED = "PAIR_LOCKED"
    GLOBAL_PAIRLOCK = "GLOBAL_PAIRLOCK"
    POSITION_ALREADY_OPEN = "POSITION_ALREADY_OPEN"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    INSUFFICIENT_STAKE = "INSUFFICIENT_STAKE"
    MIN_NOTIONAL = "MIN_NOTIONAL"
    INVALID_PRICE = "INVALID_PRICE"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    CONFIRM_ENTRY_FALSE = "CONFIRM_ENTRY_FALSE"
    BOT_PAUSED = "BOT_PAUSED"
    RISK_VETO = "RISK_VETO"
    ORDER_TIMEOUT = "ORDER_TIMEOUT"
    ORDER_CANCELLED = "ORDER_CANCELLED"
    EXCHANGE_REJECTED = "EXCHANGE_REJECTED"
    EXCHANGE_ERROR = "EXCHANGE_ERROR"
    SYSTEM_ERROR = "SYSTEM_ERROR"
    UNKNOWN = "UNKNOWN"


class DataQuality(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    LATE = "LATE"
    BACKFILLED = "BACKFILLED"
    MISSING = "MISSING"
    INVALID = "INVALID"


#: What each rejection code means, in the words the dashboard shows. The web
#: app explains everything; this is where the explanations live.
REJECTION_MEANING = {
    "MAX_OPEN_TRADES": "Every trade slot was already in use, so this signal could not be taken.",
    "PAIR_LOCKED": "This pair was locked by a protection (usually a cooldown after a loss).",
    "GLOBAL_PAIRLOCK": "All pairs were locked by a protection (too many stop-losses recently).",
    "POSITION_ALREADY_OPEN": "The bot already held a position in this pair.",
    "INSUFFICIENT_BALANCE": "There was not enough free balance to fund the trade.",
    "INSUFFICIENT_STAKE": "The stake the strategy asked for was below the exchange's minimum.",
    "MIN_NOTIONAL": "The order value was below the exchange's minimum order size.",
    "INVALID_PRICE": "The exchange refused the price on the order.",
    "INVALID_QUANTITY": "The exchange refused the amount on the order.",
    "CONFIRM_ENTRY_FALSE": "The strategy's final confirmation step said no.",
    "BOT_PAUSED": "The bot was paused, so it managed what it held but opened nothing new.",
    "RISK_VETO": "A risk rule vetoed the trade.",
    "ORDER_TIMEOUT": "The order sat unfilled past the timeout and was cancelled.",
    "ORDER_CANCELLED": "The order was cancelled before it filled.",
    "EXCHANGE_REJECTED": "The exchange rejected the order outright.",
    "EXCHANGE_ERROR": "The exchange returned an error while the order was being placed.",
    "SYSTEM_ERROR": "Something in the bot itself failed while acting on the signal.",
    "UNKNOWN": "The signal was not acted on and the bot did not say why.",
}
