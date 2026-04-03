"""
Strategy onboarding — 6-question interview flow.
Guides new users through setup and activates their first strategy.
"""

from __future__ import annotations

import json
import logging

from whatsapp_bot.core.whatsapp import WhatsAppClient
from whatsapp_bot.db import supabase_client as db
from whatsapp_bot.db.models import User
from whatsapp_bot.services.claude_engine import ClaudeEngine

logger = logging.getLogger(__name__)

STEPS = [
    "ask_name",
    "ask_yield_target",
    "ask_max_drawdown",
    "ask_trading_style",
    "ask_pairs_blacklist",
    "ask_automation_mode",
    "recommend_strategy",
    "confirm_strategy",
]


async def handle_onboarding(
    user: User, text: str, button_id: str | None,
    wa: WhatsAppClient, claude: ClaudeEngine,
):
    """State-machine onboarding flow."""
    step = user.onboarding_step or "ask_name"

    # First contact — welcome message
    if step == "ask_name" and not user.onboarding_step:
        db.update_user(user.id, {"onboarding_step": "ask_name"})
        await wa.send_text(
            user.whatsapp_number,
            "Welcome to *Evzino Trading Bot*!\n\n"
            "I'm your AI-powered crypto trading assistant. "
            "Let's get you set up in under 2 minutes.\n\n"
            "First — what's your name?"
        )
        return

    # ── Step: Name ─────────────────────────────────────────
    if step == "ask_name":
        db.update_user(user.id, {"name": text.strip(), "onboarding_step": "ask_yield_target"})
        await wa.send_interactive_buttons(
            user.whatsapp_number,
            f"Nice to meet you, *{text.strip()}*!\n\n"
            "What's your monthly yield target?\n"
            "This helps me pick the right strategy for you.",
            [
                {"id": "yield_5", "title": "~5% (Safe)"},
                {"id": "yield_10", "title": "~10% (Moderate)"},
                {"id": "yield_20", "title": "20%+ (Aggressive)"},
            ],
        )
        return

    # ── Step: Yield Target ─────────────────────────────────
    if step == "ask_yield_target":
        yield_map = {"yield_5": 5.0, "yield_10": 10.0, "yield_20": 20.0}
        yield_val = yield_map.get(button_id, _parse_number(text, 10.0))
        db.update_user(user.id, {"yield_target_pct": yield_val, "onboarding_step": "ask_max_drawdown"})
        await wa.send_interactive_buttons(
            user.whatsapp_number,
            "Got it! Now, what's the *maximum monthly loss* you're comfortable with?\n\n"
            "This is your safety floor — the bot won't exceed this.",
            [
                {"id": "dd_5", "title": "-5% (Cautious)"},
                {"id": "dd_10", "title": "-10% (Moderate)"},
                {"id": "dd_20", "title": "-20% (Risky)"},
            ],
        )
        return

    # ── Step: Max Drawdown ─────────────────────────────────
    if step == "ask_max_drawdown":
        dd_map = {"dd_5": -5.0, "dd_10": -10.0, "dd_20": -20.0}
        dd_val = dd_map.get(button_id, _parse_number(text, -10.0))
        if dd_val > 0:
            dd_val = -dd_val

        risk = "conservative" if dd_val >= -5 else ("moderate" if dd_val >= -10 else "aggressive")

        db.update_user(user.id, {
            "max_drawdown_pct": dd_val,
            "risk_tolerance": risk,
            "onboarding_step": "ask_trading_style",
        })
        await wa.send_interactive_buttons(
            user.whatsapp_number,
            "What's your preferred trading style?",
            [
                {"id": "style_active", "title": "Active"},
                {"id": "style_patient", "title": "Patient"},
            ],
        )
        return

    # ── Step: Trading Style ────────────────────────────────
    if step == "ask_trading_style":
        style = "active" if button_id == "style_active" or "active" in text.lower() else "patient"
        db.update_user(user.id, {"trading_style": style, "onboarding_step": "ask_pairs_blacklist"})
        await wa.send_text(
            user.whatsapp_number,
            "Any coins you *don't* want me to trade?\n\n"
            "Available pairs: BTC, ETH, SOL, ADA\n\n"
            "Reply with coin names to exclude, or say *None* to trade all."
        )
        return

    # ── Step: Pairs Blacklist ──────────────────────────────
    if step == "ask_pairs_blacklist":
        blacklist = []
        if "none" not in text.lower():
            for coin in ["BTC", "ETH", "SOL", "ADA"]:
                if coin.lower() in text.lower():
                    blacklist.append(f"{coin}/USDT")

        db.update_user(user.id, {
            "pairs_blacklist": json.dumps(blacklist),
            "onboarding_step": "ask_automation_mode",
        })
        await wa.send_interactive_buttons(
            user.whatsapp_number,
            "Last question! Which mode do you want to start with?\n\n"
            "*Paper Trading* — Freqtrade dry_run, no real money\n"
            "*Live Trading* — real trades on KuCoin\n\n"
            "You can switch anytime later.",
            [
                {"id": "mode_paper", "title": "Paper (Recommended)"},
                {"id": "mode_live", "title": "Live Trading"},
            ],
        )
        return

    # ── Step: Automation Mode ──────────────────────────────
    if step == "ask_automation_mode":
        mode = "live" if button_id == "mode_live" or "live" in text.lower() else "paper"
        db.update_user(user.id, {
            "automation_mode": mode,
            "onboarding_step": "recommend_strategy",
        })

        user = db.get_user_by_phone(user.whatsapp_number)
        await wa.send_text(user.whatsapp_number, "Analyzing your profile...")

        answers = {
            "name": user.name,
            "yield_target_pct": user.yield_target_pct,
            "max_drawdown_pct": user.max_drawdown_pct,
            "trading_style": user.trading_style,
            "pairs_blacklist": user.pairs_blacklist,
            "automation_mode": user.automation_mode,
        }
        recommendation = claude.recommend_strategy(answers)

        db.save_message(user.id, "assistant", recommendation)
        db.update_user(user.id, {"onboarding_step": "confirm_strategy"})

        await wa.send_text(user.whatsapp_number, recommendation)
        return

    # ── Step: Confirm Strategy ─────────────────────────────
    if step == "confirm_strategy":
        lower = text.lower().strip()

        if "activate" in lower or "yes" in lower or button_id == "btn_activate":
            await _activate_strategy(user, wa)
            return

        if "adjust" in lower:
            db.update_user(user.id, {"onboarding_step": "recommend_strategy"})
            await wa.send_text(
                user.whatsapp_number,
                "Sure! What would you like to adjust? "
                "Tell me your preference and I'll recommend again."
            )
            return

        if "explain" in lower:
            user = db.get_user_by_phone(user.whatsapp_number)
            answers = {
                "name": user.name,
                "yield_target_pct": user.yield_target_pct,
                "max_drawdown_pct": user.max_drawdown_pct,
                "trading_style": user.trading_style,
                "pairs_blacklist": user.pairs_blacklist,
                "automation_mode": user.automation_mode,
            }
            explanation = claude.recommend_strategy(answers)
            await wa.send_text(user.whatsapp_number, explanation)
            return

        # Free-text — send to GPT-4o
        user = db.get_user_by_phone(user.whatsapp_number)
        answers = {
            "name": user.name,
            "yield_target_pct": user.yield_target_pct,
            "max_drawdown_pct": user.max_drawdown_pct,
            "trading_style": user.trading_style,
            "pairs_blacklist": user.pairs_blacklist,
            "automation_mode": user.automation_mode,
        }
        recommendation = claude.recommend_strategy(answers)
        await wa.send_text(user.whatsapp_number, recommendation)
        return


async def _activate_strategy(user: User, wa: WhatsAppClient):
    """Activate the recommended strategy and complete onboarding."""
    from datetime import datetime, timezone
    from whatsapp_bot.services.strategy_manager import STRATEGY_DEFAULTS
    from whatsapp_bot.config import settings

    strategy_map = {
        "conservative": "ConservativeRSI",
        "moderate": "MeanReversionScalper",
        "aggressive": "BollingerBreakout",
    }

    risk = user.risk_tolerance or "moderate"
    strategy_name = strategy_map.get(risk, "MeanReversionScalper")
    params = STRATEGY_DEFAULTS.get(strategy_name, STRATEGY_DEFAULTS["MeanReversionScalper"])

    db.create_strategy(
        user_id=user.id,
        strategy_name=strategy_name,
        params=params,
        reason=f"Recommended during onboarding based on {risk} risk profile",
        activated_by="bot",
    )

    db.update_user(user.id, {
        "onboarded_at": datetime.now(timezone.utc).isoformat(),
        "onboarding_step": None,
    })

    strategy = db.get_active_strategy(user.id)
    strat_name = strategy.strategy_name if strategy else strategy_name

    await wa.send_text(
        user.whatsapp_number,
        f"*Setup Complete!*\n\n"
        f"Strategy: *{strat_name}*\n"
        f"Stoploss: {params['stoploss_pct']}%\n"
        f"Max Trades: {params['max_open_trades']}\n"
        f"Timeframe: {params['timeframe']}\n\n"
        f"Your bot is now active! I'll send you trade notifications "
        f"and a daily summary.\n\n"
        f"You can ask me anything — try:\n"
        f"- \"How's my bot doing?\"\n"
        f"- \"Show me BTC trend\"\n"
        f"- \"What's happening in the market?\"\n"
        f"- \"Switch to a different strategy\"\n\n"
        f"Freqtrade dashboard: {settings.freqtrade_api_url}"
    )


def _parse_number(text: str, default: float) -> float:
    """Try to extract a number from free text."""
    import re
    match = re.search(r"-?\d+\.?\d*", text)
    if match:
        return float(match.group())
    return default
