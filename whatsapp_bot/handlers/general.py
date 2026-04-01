"""
General handlers — greetings, help, fallback.
"""

from __future__ import annotations

HELP_MESSAGE = """*Neuronaq Trading Bot — Quick Guide*

Here's what you can ask me:

📊 *Portfolio & Trading*
• "How's my bot doing?"
• "Show my open trades"
• "What's my P&L today?"
• "Buy BTC" / "Sell ETH"
• "Close all trades"

📈 *Market Research*
• "What's the price of BTC?"
• "Analyze SOL"
• "What's happening in the market?"

⚙️ *Strategy*
• "Show my strategy"
• "Switch to ConservativeRSI"
• "Update stoploss to -3%"
• "Compare strategies"

🔔 *Settings*
• "Switch to live trading"
• "Turn off daily summaries"

Just type naturally — I understand plain English! 🤖"""


GREETING_RESPONSES = [
    "Hey{name}! How can I help you today? Ask me about your trades, market trends, or anything crypto!",
    "Hi{name}! Ready to trade? Ask me anything — portfolio status, market analysis, strategy changes.",
]


def get_help_message() -> str:
    return HELP_MESSAGE


def get_greeting(name: str | None = None) -> str:
    import random
    name_str = f" {name}" if name else ""
    return random.choice(GREETING_RESPONSES).format(name=name_str)
