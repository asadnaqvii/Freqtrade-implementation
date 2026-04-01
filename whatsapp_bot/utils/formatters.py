"""
Format trading data into clean WhatsApp-friendly text.
WhatsApp supports basic markdown: *bold*, _italic_, ~strikethrough~, ```code```.
"""

from __future__ import annotations
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from whatsapp_bot.db.models import TradeLog


def format_trade_notification(trade: dict, action: str) -> str:
    """Format a trade open/close event for WhatsApp."""
    pair = trade.get("pair", "???")
    price = trade.get("open_rate") if action == "opened" else trade.get("close_rate")
    profit = trade.get("profit_pct", 0)
    profit_abs = trade.get("profit_abs", 0)
    strategy = trade.get("strategy", "")

    if action == "opened":
        return (
            f"📈 *Trade Opened*\n"
            f"Pair: {pair}\n"
            f"Entry: ${price:,.2f}\n"
            f"Strategy: {strategy}"
        )
    else:
        emoji = "✅" if profit >= 0 else "❌"
        return (
            f"{emoji} *Trade Closed*\n"
            f"Pair: {pair}\n"
            f"Exit: ${price:,.2f}\n"
            f"P&L: {profit:+.2f}% (${profit_abs:+.2f})\n"
            f"Strategy: {strategy}"
        )


def format_open_trades(trades: list[dict]) -> str:
    """Format a list of open trades as a summary."""
    if not trades:
        return "No open trades right now."

    lines = ["*Open Trades*\n"]
    for t in trades:
        pair = t.get("pair", "???")
        profit = t.get("profit_pct", 0)
        profit_abs = t.get("profit_abs", 0)
        emoji = "🟢" if profit >= 0 else "🔴"
        lines.append(f"{emoji} {pair}: {profit:+.2f}% (${profit_abs:+.2f})")
    return "\n".join(lines)


def format_daily_summary(data: dict) -> str:
    """Format daily P&L summary."""
    trades_count = data.get("trades_count", 0)
    wins = data.get("wins", 0)
    losses = data.get("losses", 0)
    profit = data.get("profit", 0)
    balance = data.get("balance", 0)

    win_rate = (wins / trades_count * 100) if trades_count > 0 else 0

    return (
        f"📊 *Daily Summary*\n"
        f"Date: {datetime.utcnow().strftime('%Y-%m-%d')}\n\n"
        f"Trades: {trades_count}\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Win Rate: {win_rate:.0f}%\n"
        f"Net P&L: ${profit:+.2f}\n"
        f"Balance: ${balance:,.2f}"
    )


def format_portfolio(balance: dict, profit: dict) -> str:
    """Format portfolio overview."""
    total = balance.get("total", 0)
    currencies = balance.get("currencies", [])

    lines = [
        f"💰 *Portfolio Summary*\n",
        f"Total Value: ${total:,.2f}",
        f"Total Profit: ${profit.get('profit_all_coin', 0):+.4f} USDT",
        f"Today's Profit: ${profit.get('profit_closed_coin', 0):+.4f} USDT",
        "",
    ]

    if currencies:
        lines.append("*Holdings:*")
        for c in currencies:
            symbol = c.get("currency", "")
            bal = c.get("balance", 0)
            if bal > 0:
                lines.append(f"  {symbol}: {bal:.4f}")

    return "\n".join(lines)


def format_strategy_info(name: str, params: dict) -> str:
    """Format strategy details for display."""
    return (
        f"📋 *Strategy: {name}*\n\n"
        f"Stoploss: {params.get('stoploss_pct', 'N/A')}%\n"
        f"Max Trades: {params.get('max_open_trades', 'N/A')}\n"
        f"Timeframe: {params.get('timeframe', 'N/A')}\n"
        f"ROI: {params.get('roi_config', {})}\n"
        f"Activated by: {params.get('activated_by', 'N/A')}\n"
        f"Reason: {params.get('reason', 'N/A')}"
    )


def format_macro_alert(event: dict) -> str:
    """Format a macro intelligence alert."""
    fg = event.get("fear_greed_score", "?")
    rec = event.get("claude_recommendation", "")
    headlines = event.get("headlines", [])

    headline_text = "\n".join(f"• {h}" for h in headlines[:5])

    return (
        f"⚠️ *Macro Alert — Strategy Review Needed*\n\n"
        f"Fear & Greed Index: {fg}/100\n\n"
        f"*Top Headlines:*\n{headline_text}\n\n"
        f"*Analysis:*\n{rec}\n\n"
        f"Reply: *Agree* / *Disagree* / *Let's discuss*"
    )


# ── TradeLog-based formatters (per-user trade_logs) ──────


def format_trade_log_entry(trade: TradeLog) -> str:
    """Format a single TradeLog entry for WhatsApp."""
    pair = trade.pair or "???"
    side = (trade.side or "long").upper()

    if trade.is_open:
        return (
            f"📈 *Trade #{trade.trade_id} — {side} {pair}*\n"
            f"Entry: ${trade.price:,.2f}\n"
            f"Stake: ${trade.stake_amount:,.2f} USDT\n"
            f"Amount: {trade.amount:.6f}\n"
            f"Strategy: {trade.strategy or 'Manual'}"
        )
    else:
        profit = trade.profit or 0
        emoji = "✅" if profit >= 0 else "❌"
        exit_p = trade.exit_price or 0
        profit_pct = ((exit_p / trade.price) - 1) * 100 if trade.price else 0
        return (
            f"{emoji} *Trade #{trade.trade_id} — {pair}*\n"
            f"Entry: ${trade.price:,.2f} → Exit: ${exit_p:,.2f}\n"
            f"P&L: ${profit:+,.4f} ({profit_pct:+.2f}%)\n"
            f"Strategy: {trade.strategy or 'Manual'}"
        )


def format_trade_log_list(trades: list[TradeLog], title: str = "Trade History") -> str:
    """Format a list of TradeLog entries."""
    if not trades:
        return "No trades found for this period."

    lines = [f"*{title}*\n"]
    for t in trades:
        lines.append(format_trade_log_entry(t))
        lines.append("")  # blank line separator
    return "\n".join(lines).rstrip()


def format_profit_summary(summary: dict, mode: str = "paper") -> str:
    """Format a profit summary dict (from FreqtradeClient.get_profit)."""
    mode_label = "Paper" if mode == "paper" else "Live"
    return (
        f"*Performance Summary ({mode_label})*\n"
        f"{'=' * 28}\n\n"
        f"Total Trades: {summary.get('total_trades', 0)}\n"
        f"Wins: {summary.get('wins', 0)} | Losses: {summary.get('losses', 0)}\n"
        f"Win Rate: {summary.get('win_rate', 0)}%\n\n"
        f"Net Profit: ${summary.get('total_profit_usdt', 0):+,.4f} USDT\n"
        f"Best Trade: ${summary.get('best_trade_usdt', 0):+,.4f}\n"
        f"Worst Trade: ${summary.get('worst_trade_usdt', 0):+,.4f}"
    )


def format_balance(balance: dict) -> str:
    """Format balance dict (from FreqtradeClient.get_balance)."""
    mode = balance.get("mode", "paper")
    if mode == "paper":
        return (
            f"💰 *Paper Trading Balance*\n\n"
            f"Available: ${balance.get('balance_usdt', 0):,.2f} USDT\n"
            f"Unrealized P&L: ${balance.get('unrealized_pnl', 0):+,.4f}\n"
            f"Total Value: ${balance.get('total', 0):,.2f}\n"
            f"Open Positions: {balance.get('open_positions', 0)}"
        )
    else:
        return (
            f"💰 *KuCoin Balance*\n\n"
            f"Available: ${balance.get('balance_usdt', 0):,.2f} USDT\n"
            f"In Use: ${balance.get('used_usdt', 0):,.2f}\n"
            f"Total: ${balance.get('total_usdt', 0):,.2f}\n"
            f"Open Positions: {balance.get('open_positions', 0)}"
        )


def format_account_status(status: dict) -> str:
    """Format full account status for WhatsApp."""
    mode = status.get("mode", "paper")
    mode_emoji = "📝" if mode == "paper" else "💰"
    kucoin = "Connected" if status.get("kucoin_connected") else "Not connected"

    balance = status.get("balance", {})
    bal_str = f"${balance.get('balance_usdt', 0):,.2f}"

    return (
        f"*Account Status*\n"
        f"{'=' * 20}\n\n"
        f"Mode: {mode_emoji} {mode.title()} Trading\n"
        f"KuCoin: {kucoin}\n"
        f"Balance: {bal_str} USDT\n"
        f"Open Positions: {status.get('open_positions', 0)}\n"
        f"Strategy: {status.get('active_strategy') or 'None'}\n"
        f"Risk Profile: {status.get('risk_tolerance', 'N/A')}\n"
        f"Yield Target: {status.get('yield_target_pct', 'N/A')}%"
    )
