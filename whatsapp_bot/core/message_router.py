"""
Message router — dispatches incoming messages to the appropriate handler.
Checks user state (onboarding vs active) and routes accordingly.
"""

from __future__ import annotations

import json
import logging

from whatsapp_bot.core.auth import get_or_create_user, is_onboarded
from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db.models import User
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.services.claude_engine import ClaudeEngine
from whatsapp_bot.services.freqtrade_client import get_freqtrade_client
from whatsapp_bot.services.user_memory import UserMemory
from whatsapp_bot.handlers.onboarding import handle_onboarding
from whatsapp_bot.utils.error_handler import handle_error

logger = logging.getLogger(__name__)


class MessageRouter:
    def __init__(self):
        self.wa = WhatsAppClient()
        self.claude = ClaudeEngine()
        self.ft = get_freqtrade_client()

    async def route(self, phone: str, text: str, msg_type: str = "text", button_id: str | None = None):
        """Main entry point — route an incoming message."""
        try:
            user = await get_or_create_user(phone)

            if not is_onboarded(user):
                await handle_onboarding(user, text, button_id, self.wa, self.claude)
                return

            await self._handle_active_user(user, text)

        except Exception as exc:
            await handle_error(exc, self.wa, phone)

    async def _handle_active_user(self, user: User, text: str):
        """Handle messages from onboarded users via GPT-4o with tool use."""
        memory = UserMemory(user)
        strategy = memory.get_active_strategy()
        history = memory.get_recent_messages(limit=20)

        async def execute_tool(name: str, inputs: dict) -> str:
            return await self._execute_tool(name, inputs, user)

        response = await self.claude.chat(
            user=user,
            strategy=strategy,
            message=text,
            history=history,
            tool_executor=execute_tool,
        )

        memory.save_user_and_assistant(text, response)
        await self.wa.send_text(user.whatsapp_number, response)

    async def _execute_tool(self, name: str, inputs: dict, user: User) -> str:
        """Execute a tool called by GPT-4o and return the result as a string."""
        try:
            if name == "get_bot_status":
                result = await self.ft.get_status()
                return json.dumps(result, default=str)

            elif name == "get_profit_summary":
                result = await self.ft.get_profit()
                return json.dumps(result, default=str)

            elif name == "get_balance":
                result = await self.ft.get_balance()
                return json.dumps(result, default=str)

            elif name == "get_trade_history":
                limit = inputs.get("limit", 20)
                result = await self.ft.get_trades(limit=limit)
                return json.dumps(result, default=str)

            elif name == "force_entry":
                pair = inputs["pair"]
                side = inputs.get("side", "long")
                stake = inputs.get("stake_amount")
                result = await self.ft.force_entry(pair, side, stake)
                return json.dumps(result, default=str)

            elif name == "force_exit":
                trade_id = int(inputs["trade_id"])
                result = await self.ft.force_exit(trade_id)
                return json.dumps(result, default=str)

            elif name == "switch_strategy":
                from whatsapp_bot.services.strategy_manager import StrategyManager
                mgr = StrategyManager()
                result = await mgr.switch_strategy(
                    user_id=user.id,
                    strategy_name=inputs["strategy_name"],
                    reason=inputs.get("reason", "User requested"),
                    activated_by="user",
                    ft_client=self.ft,
                )
                return f"Strategy switched to {inputs['strategy_name']}. {result}"

            elif name == "update_strategy_params":
                from whatsapp_bot.services.strategy_manager import StrategyManager
                mgr = StrategyManager()
                result = await mgr.update_params(
                    user_id=user.id, updates=inputs, ft_client=self.ft,
                )
                return f"Strategy parameters updated. {result}"

            elif name == "get_market_price":
                from whatsapp_bot.services.market_analysis import MarketAnalysis
                ma = MarketAnalysis()
                result = await ma.get_price(inputs["pair"])
                return json.dumps(result, default=str)

            elif name == "analyze_trend":
                from whatsapp_bot.services.market_analysis import MarketAnalysis
                ma = MarketAnalysis()
                result = await ma.analyze(inputs["pair"])
                return json.dumps(result, default=str)

            elif name == "get_account_status":
                from whatsapp_bot.config import settings
                balance = await self.ft.get_balance()
                open_trades = await self.ft.get_status()
                bot_state = await self.ft.get_state()
                strategy = db.get_active_strategy(user.id)
                status = {
                    "freqtrade_url": settings.freqtrade_api_url,
                    "bot_state": bot_state,
                    "balance": balance,
                    "open_trades": len(open_trades),
                    "active_strategy": strategy.strategy_name if strategy else None,
                    "risk_tolerance": user.risk_tolerance,
                    "yield_target_pct": user.yield_target_pct,
                }
                return json.dumps(status, default=str)

            else:
                return f"Unknown tool: {name}"

        except Exception as e:
            logger.error("Tool execution error [%s]: %s", name, e)
            return f"Error executing {name}: {e}"
