"""
Chart generator — renders trading charts as PNG bytes for WhatsApp.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime

import matplotlib
matplotlib.use("Agg")  # Non-interactive backend
import matplotlib.pyplot as plt
import matplotlib.dates as mdates

logger = logging.getLogger(__name__)


def generate_candlestick_chart(
    ohlcv: list[list], pair: str, indicators: dict | None = None
) -> bytes:
    """
    Generate a candlestick-style chart with optional indicator overlays.
    ohlcv: list of [timestamp, open, high, low, close, volume]
    indicators: {"bb_upper": [...], "bb_lower": [...], "ema_50": [...], etc.}
    Returns PNG bytes.
    """
    if not ohlcv:
        return _empty_chart("No data available")

    timestamps = [datetime.fromtimestamp(c[0] / 1000) for c in ohlcv]
    opens = [c[1] for c in ohlcv]
    highs = [c[2] for c in ohlcv]
    lows = [c[3] for c in ohlcv]
    closes = [c[4] for c in ohlcv]

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), height_ratios=[3, 1],
                                    gridspec_kw={"hspace": 0.3})
    fig.patch.set_facecolor("#1a1a2e")

    # Price chart
    ax1.set_facecolor("#16213e")
    colors = ["#00ff88" if c >= o else "#ff4444" for o, c in zip(opens, closes)]
    ax1.bar(timestamps, [abs(c - o) or 0.01 for o, c in zip(opens, closes)],
            bottom=[min(o, c) for o, c in zip(opens, closes)],
            color=colors, width=0.0005, alpha=0.8)
    ax1.vlines(timestamps, lows, highs, colors=colors, linewidth=0.5)

    # Indicator overlays
    if indicators:
        if "bb_upper" in indicators and indicators["bb_upper"]:
            ax1.plot(timestamps[-len(indicators["bb_upper"]):], indicators["bb_upper"],
                     color="#4488ff", linewidth=0.8, alpha=0.6, label="BB Upper")
        if "bb_lower" in indicators and indicators["bb_lower"]:
            ax1.plot(timestamps[-len(indicators["bb_lower"]):], indicators["bb_lower"],
                     color="#4488ff", linewidth=0.8, alpha=0.6, label="BB Lower")
        if "ema_50" in indicators and indicators["ema_50"]:
            ax1.plot(timestamps[-len(indicators["ema_50"]):], indicators["ema_50"],
                     color="#ffaa00", linewidth=1, alpha=0.8, label="EMA 50")

    ax1.set_title(f"{pair} Chart", color="white", fontsize=12, fontweight="bold")
    ax1.tick_params(colors="white", labelsize=8)
    ax1.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax1.legend(fontsize=7, loc="upper left", facecolor="#16213e", edgecolor="#333",
               labelcolor="white")

    # Volume chart
    volumes = [c[5] for c in ohlcv]
    ax2.set_facecolor("#16213e")
    ax2.bar(timestamps, volumes, color=colors, width=0.0005, alpha=0.6)
    ax2.set_title("Volume", color="white", fontsize=10)
    ax2.tick_params(colors="white", labelsize=7)
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))

    for ax in [ax1, ax2]:
        for spine in ax.spines.values():
            spine.set_color("#333")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def generate_pnl_chart(daily_pnl: list[dict]) -> bytes:
    """
    Generate a P&L over time chart.
    daily_pnl: [{"date": "2026-03-15", "profit": 4.20}, ...]
    """
    if not daily_pnl:
        return _empty_chart("No P&L data available")

    dates = [datetime.strptime(d["date"], "%Y-%m-%d") for d in daily_pnl]
    profits = [d["profit"] for d in daily_pnl]
    cumulative = []
    total = 0
    for p in profits:
        total += p
        cumulative.append(total)

    fig, ax = plt.subplots(figsize=(10, 4))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    colors = ["#00ff88" if p >= 0 else "#ff4444" for p in profits]
    ax.bar(dates, profits, color=colors, alpha=0.6, width=0.8, label="Daily P&L")
    ax.plot(dates, cumulative, color="#ffaa00", linewidth=2, label="Cumulative")

    ax.set_title("Profit & Loss", color="white", fontsize=12, fontweight="bold")
    ax.tick_params(colors="white", labelsize=8)
    ax.axhline(y=0, color="#666", linewidth=0.5)
    ax.legend(fontsize=8, facecolor="#16213e", edgecolor="#333", labelcolor="white")

    for spine in ax.spines.values():
        spine.set_color("#333")

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=120, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _empty_chart(message: str) -> bytes:
    """Generate a placeholder chart with a message."""
    fig, ax = plt.subplots(figsize=(6, 3))
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")
    ax.text(0.5, 0.5, message, ha="center", va="center", color="white", fontsize=14)
    ax.set_xticks([])
    ax.set_yticks([])

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=100, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()
