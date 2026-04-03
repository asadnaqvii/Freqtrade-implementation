"""
AI engine — OpenAI GPT-4o integration with function calling.
Handles NLU, strategy reasoning, macro analysis, and recommendations.

Note: File kept as claude_engine.py to avoid renaming all imports.
Internally uses OpenAI SDK. Can be swapped to Anthropic later.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from openai import OpenAI

from whatsapp_bot.config import settings
from whatsapp_bot.db.models import User, Strategy, ConversationMessage

logger = logging.getLogger(__name__)

# ── Tool Definitions (OpenAI function calling format) ──────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_bot_status",
            "description": "Get the current status of all open trades including pair, P&L, and entry price.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_profit_summary",
            "description": "Get overall profit/loss summary including total profit, trade count, and win rate.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_balance",
            "description": "Get current portfolio balance and holdings breakdown.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_trade_history",
            "description": "Get recent trade history with details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Number of trades to fetch (default 20)"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "force_entry",
            "description": "Open a new trade on a specific pair. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string", "description": "Trading pair e.g. BTC/USDT"},
                    "side": {"type": "string", "enum": ["long"], "description": "Trade direction"},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "force_exit",
            "description": "Close an open trade by trade ID. Requires user confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trade_id": {"type": "integer", "description": "The trade ID to close"},
                },
                "required": ["trade_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "switch_strategy",
            "description": "Switch to a different trading strategy. Available: ConservativeRSI, EMACrossover, BollingerBreakout, ActiveTrader, MeanReversionScalper.",
            "parameters": {
                "type": "object",
                "properties": {
                    "strategy_name": {"type": "string", "description": "Name of the strategy to activate"},
                    "reason": {"type": "string", "description": "Why this strategy is being recommended"},
                },
                "required": ["strategy_name", "reason"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_strategy_params",
            "description": "Update strategy parameters like stoploss, max trades, or timeframe.",
            "parameters": {
                "type": "object",
                "properties": {
                    "stoploss_pct": {"type": "number", "description": "Stop loss percentage (negative, e.g. -5.0)"},
                    "max_open_trades": {"type": "integer", "description": "Maximum concurrent trades"},
                    "timeframe": {"type": "string", "description": "Candle timeframe e.g. 1m, 5m, 15m, 1h"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_price",
            "description": "Get the current price and 24h stats for a trading pair.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string", "description": "Trading pair e.g. BTC/USDT"},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_trend",
            "description": "Run technical analysis on a pair — RSI, EMA, MACD, Bollinger Bands.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pair": {"type": "string", "description": "Trading pair e.g. BTC/USDT"},
                },
                "required": ["pair"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_account_status",
            "description": "Get full account status: trading mode, KuCoin connection, balance, active strategy, and open positions count.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


# ── System Prompt Builder ──────────────────────────────────

def build_system_prompt(user: User, strategy: Strategy | None) -> str:
    """Build a context-rich system prompt."""
    strategy_info = "No active strategy configured."
    if strategy:
        strategy_info = (
            f"Active Strategy: {strategy.strategy_name}\n"
            f"  Stoploss: {strategy.stoploss_pct}%\n"
            f"  Max Open Trades: {strategy.max_open_trades}\n"
            f"  Timeframe: {strategy.timeframe}\n"
            f"  ROI Config: {json.dumps(strategy.roi_config)}\n"
            f"  Activated by: {strategy.activated_by}\n"
            f"  Reason: {strategy.reason or 'N/A'}"
        )

    from whatsapp_bot.config import settings

    return f"""You are the Evzino Trading Assistant — an AI-powered crypto trading bot operating inside WhatsApp.

PERSONALITY:
- Friendly, concise, and professional
- Explain complex concepts simply when the user is learning
- Always include risk disclaimers when making recommendations
- Never guarantee profits or make promises about returns
- Use WhatsApp formatting: *bold*, _italic_ for emphasis

USER PROFILE:
- Name: {user.name or 'Not set'}
- Risk Tolerance: {user.risk_tolerance}
- Monthly Yield Target: {user.yield_target_pct}%
- Max Drawdown: {user.max_drawdown_pct}%
- Trading Style: {user.trading_style}
- Mode: {user.automation_mode} trading
- Excluded Pairs: {json.dumps(user.pairs_blacklist)}

{strategy_info}

SETUP:
Freqtrade is running and connected. It auto-trades using the active strategy on KuCoin.
Dashboard available at: {settings.freqtrade_api_url}

AVAILABLE STRATEGIES:
1. ConservativeRSI — Low risk, RSI < 30 entries above 200 EMA, -5% stoploss
2. EMACrossover — Low-medium risk, trend-following EMA golden cross, -7% stoploss
3. BollingerBreakout — Medium risk, volatility breakout with BB + Stochastic, -8% stoploss
4. ActiveTrader — Medium-high risk, fast 1m signals for active trading, -3% stoploss
5. MeanReversionScalper — Medium risk, scalps oversold bounces with BB + RSI, -0.4% stoploss

AVAILABLE PAIRS: BTC/USDT, ETH/USDT, SOL/USDT, ADA/USDT

RULES:
- For force_entry and force_exit: ALWAYS ask for explicit user confirmation before executing.
  Present the trade details and ask "Shall I proceed? (Yes/No)"
- For switch_strategy and update_strategy_params: explain what will change and ask for confirmation.
- When the user asks about the market or trends, use the analyze_trend tool.
- Keep responses concise — WhatsApp messages should be scannable, not essays.
- When the user asks to see their dashboard, give them the Freqtrade URL.
- Use get_account_status to provide a full overview when the user asks about their account.
"""


# ── Main Chat Class ────────────────────────────────────────

class ClaudeEngine:
    """
    AI engine using OpenAI GPT-4o with function calling.
    Class name kept as ClaudeEngine to avoid renaming all imports across the codebase.
    """

    def __init__(self):
        self.client = OpenAI(api_key=settings.openai_api_key)

    async def chat(
        self,
        user: User,
        strategy: Strategy | None,
        message: str,
        history: list[ConversationMessage],
        tool_executor=None,
    ) -> str:
        """
        Send a message to GPT-4o with full context and tool use.
        tool_executor: async callable(tool_name, tool_input) -> tool_result_str
        Returns the final text response.
        """
        system_prompt = build_system_prompt(user, strategy)

        # Build messages from conversation history
        messages = [{"role": "system", "content": system_prompt}]
        for msg in history:
            messages.append({"role": msg.role, "content": msg.message})
        messages.append({"role": "user", "content": message})

        # Initial GPT-4o call
        response = self.client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=1024,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
        )

        resp_message = response.choices[0].message

        # Handle tool use loop (GPT-4o may call multiple tools)
        while resp_message.tool_calls:
            # Add assistant message with tool calls
            messages.append(resp_message)

            for tool_call in resp_message.tool_calls:
                tool_name = tool_call.function.name
                tool_input = json.loads(tool_call.function.arguments)
                logger.info("GPT-4o calling tool: %s(%s)", tool_name, tool_input)

                if tool_executor:
                    try:
                        result = await tool_executor(tool_name, tool_input)
                    except Exception as e:
                        result = f"Error: {e}"
                else:
                    result = "Tool execution not available."

                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call.id,
                    "content": str(result),
                })

            # Continue conversation with tool results
            response = self.client.chat.completions.create(
                model=settings.openai_model,
                max_tokens=1024,
                messages=messages,
                tools=TOOLS,
                tool_choice="auto",
            )
            resp_message = response.choices[0].message

        return resp_message.content or "I processed your request but have no additional response."

    def recommend_strategy(self, onboarding_answers: dict) -> str:
        """
        Generate a strategy recommendation from onboarding answers.
        Returns the full recommendation text.
        """
        prompt = f"""Based on these user preferences, recommend the BEST trading strategy:

User Preferences:
- Name: {onboarding_answers.get('name', 'User')}
- Monthly yield target: {onboarding_answers.get('yield_target_pct', 10)}%
- Max monthly drawdown: {onboarding_answers.get('max_drawdown_pct', -10)}%
- Trading style: {onboarding_answers.get('trading_style', 'active')}
- Pairs to avoid: {onboarding_answers.get('pairs_blacklist', [])}
- Mode: {onboarding_answers.get('automation_mode', 'paper')} trading

Available strategies:
1. ConservativeRSI — Low risk, 5m, RSI<30 + EMA200 filter, -5% SL, 5-15% monthly
2. EMACrossover — Low-medium risk, 15m, golden cross trend following, -7% SL, 10-25% monthly
3. BollingerBreakout — Medium risk, 5m, volatility breakout, -8% SL, 15-35% monthly
4. ActiveTrader — Medium-high risk, 1m, frequent signals, -3% SL, high activity
5. MeanReversionScalper — Medium risk, 5m, oversold scalping, -0.4% SL, tight TP/SL

Respond with EXACTLY this structure:
1. Strategy name and one-line description
2. Parameters: stoploss %, max concurrent trades, timeframe
3. Expected monthly return range and approximate win rate
4. Plain-English rationale linking the user's goals to the strategy logic
5. End with: "Ready to activate? Reply *Activate*, *Adjust*, or *Explain more*"
"""

        response = self.client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=800,
            messages=[
                {"role": "system", "content": "You are a crypto strategy advisor for Evzino Trading Bot."},
                {"role": "user", "content": prompt},
            ],
        )

        return response.choices[0].message.content

    def analyze_macro(self, headlines: list[str], fear_greed: int, strategy: Strategy) -> str:
        """
        Analyze macro conditions against active strategy.
        Returns recommendation text.
        """
        prompt = f"""You are a crypto macro analyst. Analyze these market conditions against the user's active strategy and recommend whether adjustments are needed.

Current Strategy: {strategy.strategy_name}
  Stoploss: {strategy.stoploss_pct}%
  Max Open Trades: {strategy.max_open_trades}
  Timeframe: {strategy.timeframe}

Fear & Greed Index: {fear_greed}/100 (0=Extreme Fear, 100=Extreme Greed)

Latest Headlines:
{chr(10).join(f'- {h}' for h in headlines[:10])}

If conditions warrant strategy adjustment, provide:
1. Reason for the alert
2. Current strategy assessment
3. Specific recommended parameter changes (e.g., reduce max trades, tighten stoploss)
4. End with asking the user to reply: Agree / Disagree / Let's discuss

If no adjustment needed, respond with "NO_ADJUSTMENT_NEEDED" only.
"""

        response = self.client.chat.completions.create(
            model=settings.openai_model,
            max_tokens=600,
            messages=[
                {"role": "system", "content": "You are a crypto macro analyst for Evzino Trading Bot."},
                {"role": "user", "content": prompt},
            ],
        )

        return response.choices[0].message.content
