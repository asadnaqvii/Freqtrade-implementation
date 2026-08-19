"""Compare what the bot thinks happened against what the exchange says happened.

Freqtrade's database is the bot's own account of its trades. The exchange's
order history is the authoritative one. When they disagree -- a partial fill the
bot recorded as complete, a fee it estimated rather than read, an order that
never reached the venue -- the difference shows up in reported P&L and nowhere
else until it is large.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.providers.base import OrderInfo, ProviderError, WalletProvider

log = logging.getLogger(__name__)

# Below this, a difference is rounding or a precision step, not a discrepancy.
AMOUNT_TOLERANCE = 1e-8
PRICE_TOLERANCE_PCT = 0.001  # 0.1%


@dataclass
class Discrepancy:
    pair: str
    kind: str
    ft_order_id: str | None
    exchange_order_id: str | None
    detail: str
    ft_value: float | None = None
    exchange_value: float | None = None
    pct: float | None = None

    def as_row(self, run_id: str, bot_instance_id: str | None, account_id: str | None) -> dict[str, Any]:
        return {
            "run_id": run_id,
            "bot_instance_id": bot_instance_id,
            "account_id": account_id,
            "pair": self.pair,
            "ft_order_id": self.ft_order_id,
            "exchange_order_id": self.exchange_order_id,
            "matched": self.kind == "matched",
            "discrepancy_kind": None if self.kind == "matched" else self.kind,
            "discrepancy_pct": self.pct,
            "notes": self.detail,
        }


def _pct_diff(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or a == 0:
        return None
    return abs(a - b) / abs(a)


def reconcile_orders(
    provider: WalletProvider,
    bot_orders: Iterable[dict[str, Any]],
    *,
    lookback_days: int = 30,
) -> list[Discrepancy]:
    """Match freqtrade's orders against the venue's, and report the gaps.

    `bot_orders` are rows shaped like public.v_live_orders.
    """
    by_pair: dict[str, list[dict[str, Any]]] = {}
    for order in bot_orders:
        pair = order.get("pair")
        if pair:
            by_pair.setdefault(pair, []).append(order)

    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    findings: list[Discrepancy] = []

    for pair, orders in by_pair.items():
        try:
            venue_orders = provider.fetch_orders(pair, since=since, limit=200)
        except ProviderError as exc:
            findings.append(
                Discrepancy(
                    pair=pair, kind="unavailable",
                    ft_order_id=None, exchange_order_id=None,
                    detail=f"Could not read this pair's orders from the venue: {exc}",
                )
            )
            continue

        venue_by_id: dict[str, OrderInfo] = {o.order_id: o for o in venue_orders if o.order_id}
        seen_ids: set[str] = set()

        for order in orders:
            exchange_id = str(order.get("exchange_order_id") or "")
            if not exchange_id:
                # No venue id recorded at all: either it never left the bot, or
                # the bot crashed between placing and recording.
                findings.append(
                    Discrepancy(
                        pair=pair, kind="missing_on_exchange",
                        ft_order_id=str(order.get("ft_order_id") or ""),
                        exchange_order_id=None,
                        detail="The bot recorded this order with no exchange order id.",
                    )
                )
                continue

            venue = venue_by_id.get(exchange_id)
            if venue is None:
                findings.append(
                    Discrepancy(
                        pair=pair, kind="missing_on_exchange",
                        ft_order_id=str(order.get("ft_order_id") or ""),
                        exchange_order_id=exchange_id,
                        detail=(
                            "The bot has this order but the venue does not report it "
                            f"in the last {lookback_days} days."
                        ),
                    )
                )
                continue

            seen_ids.add(exchange_id)
            findings.extend(_compare(pair, order, venue))

        for order_id, venue in venue_by_id.items():
            if order_id in seen_ids:
                continue
            # An order the venue knows about that the bot does not is the more
            # alarming direction: something placed orders on this account.
            findings.append(
                Discrepancy(
                    pair=pair, kind="missing_in_bot",
                    ft_order_id=None, exchange_order_id=order_id,
                    detail=(
                        f"The venue reports a {venue.side or 'unknown'} order "
                        f"({venue.status or 'unknown status'}) the bot has no record of. "
                        "Another bot, a manual trade, or a compromised key."
                    ),
                )
            )

    return findings


def _compare(pair: str, bot: dict[str, Any], venue: OrderInfo) -> list[Discrepancy]:
    out: list[Discrepancy] = []
    ft_order_id = str(bot.get("ft_order_id") or "")

    bot_filled = _num(bot.get("filled"))
    if bot_filled is not None and venue.filled is not None:
        if abs(bot_filled - venue.filled) > AMOUNT_TOLERANCE:
            out.append(
                Discrepancy(
                    pair=pair, kind="amount",
                    ft_order_id=ft_order_id, exchange_order_id=venue.order_id,
                    detail="Filled amount differs between the bot and the venue.",
                    ft_value=bot_filled, exchange_value=venue.filled,
                    pct=_pct_diff(bot_filled, venue.filled),
                )
            )

    bot_price = _num(bot.get("average")) or _num(bot.get("price"))
    venue_price = venue.average or venue.price
    if bot_price and venue_price:
        pct = _pct_diff(bot_price, venue_price)
        if pct is not None and pct > PRICE_TOLERANCE_PCT:
            out.append(
                Discrepancy(
                    pair=pair, kind="price",
                    ft_order_id=ft_order_id, exchange_order_id=venue.order_id,
                    detail="Execution price differs by more than the tolerance.",
                    ft_value=bot_price, exchange_value=venue_price, pct=pct,
                )
            )

    bot_status = (bot.get("status") or "").lower()
    venue_status = (venue.status or "").lower()
    if bot_status and venue_status and bot_status != venue_status:
        # 'closed' and 'filled' mean the same thing on different venues.
        equivalent = {"closed", "filled", "done"}
        if not ({bot_status, venue_status} <= equivalent):
            out.append(
                Discrepancy(
                    pair=pair, kind="status",
                    ft_order_id=ft_order_id, exchange_order_id=venue.order_id,
                    detail=f"Bot says {bot_status!r}, venue says {venue_status!r}.",
                )
            )

    if not out:
        out.append(
            Discrepancy(
                pair=pair, kind="matched",
                ft_order_id=ft_order_id, exchange_order_id=venue.order_id,
                detail="Bot and venue agree on amount, price and status.",
            )
        )
    return out


def _num(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
