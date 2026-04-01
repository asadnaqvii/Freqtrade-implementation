"""
Pydantic models for type safety across the application.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


# ── Users ──────────────────────────────────────────────────

class User(BaseModel):
    id: str
    whatsapp_number: str
    name: Optional[str] = None
    role: str = "trader"
    risk_tolerance: str = "moderate"
    yield_target_pct: float = 10.0
    max_drawdown_pct: float = -10.0
    trading_style: str = "active"
    pairs_blacklist: list[str] = Field(default_factory=list)
    automation_mode: str = "paper"
    notifications_config: dict = Field(default_factory=lambda: {
        "daily": True, "weekly": True, "monthly": True,
        "macro_alerts": True, "trade_notifications": True,
        "summary_time": "08:00",
    })
    onboarding_step: Optional[str] = None
    onboarded_at: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


# ── Strategies ─────────────────────────────────────────────

class Strategy(BaseModel):
    id: str
    user_id: str
    strategy_name: str
    stoploss_pct: float = -5.0
    max_open_trades: int = 3
    timeframe: str = "5m"
    roi_config: dict = Field(default_factory=lambda: {"0": 0.05})
    activated_at: Optional[datetime] = None
    activated_by: str = "bot"
    reason: Optional[str] = None
    is_active: bool = True


# ── Conversations ──────────────────────────────────────────

class ConversationMessage(BaseModel):
    id: Optional[str] = None
    user_id: str
    role: str          # "user" or "assistant"
    message: str
    metadata: dict = Field(default_factory=dict)
    created_at: Optional[datetime] = None


# ── Macro Events ──────────────────────────────────────────

class MacroEvent(BaseModel):
    id: Optional[str] = None
    scanned_at: Optional[datetime] = None
    headlines: list[str] = Field(default_factory=list)
    fear_greed_score: Optional[int] = None
    significance_triggered: bool = False
    claude_recommendation: Optional[str] = None
    alert_sent: bool = False
    user_response: Optional[str] = None
    final_action_taken: Optional[str] = None


# ── Trade Logs ────────────────────────────────────────────

class TradeLog(BaseModel):
    id: Optional[str] = None
    user_id: Optional[str] = None
    trade_id: Optional[int] = None
    pair: Optional[str] = None
    action: Optional[str] = None      # buy, sell, force_buy, force_sell
    side: str = "long"
    amount: Optional[float] = None     # quantity of coin
    stake_amount: Optional[float] = None  # USDT invested
    price: Optional[float] = None      # entry price
    exit_price: Optional[float] = None # exit price (null while open)
    profit: Optional[float] = None     # realized P&L in USDT (null while open)
    is_open: bool = True               # true = position still open
    strategy: Optional[str] = None
    triggered_via: str = "bot"         # bot, user, macro
    created_at: Optional[datetime] = None


# ── Onboarding State ─────────────────────────────────────

class OnboardingAnswers(BaseModel):
    """Accumulates answers during the 6-question interview."""
    name: Optional[str] = None
    yield_target_pct: Optional[float] = None
    max_drawdown_pct: Optional[float] = None
    trading_style: Optional[str] = None
    pairs_blacklist: list[str] = Field(default_factory=list)
    automation_mode: Optional[str] = None
