from __future__ import annotations

import logging
from datetime import datetime, timezone

from config import settings
from src.markets.models import (
    ArbitrageOpportunity,
    Direction,
    MatchedMarket,
    Outcome,
)

logger = logging.getLogger(__name__)


def _effective_cost(
    yes_price: float,
    no_price: float,
    yes_fee_rate: float,
    no_fee_rate: float,
) -> float:
    """Calculate total cost including worst-case fees on the winning leg.

    When the event resolves, exactly one leg pays $1.00.
    Fees are charged on profit (payout - cost) of the winning leg.
    Worst-case fee = max(fee_on_yes_win, fee_on_no_win).
    """
    # If YES wins: payout $1 from yes side, lose no_price
    fee_if_yes_wins = max(0, (1.0 - yes_price)) * yes_fee_rate
    # If NO wins: payout $1 from no side, lose yes_price
    fee_if_no_wins = max(0, (1.0 - no_price)) * no_fee_rate

    worst_fee = max(fee_if_yes_wins, fee_if_no_wins)
    return yes_price + no_price + worst_fee


def detect_opportunities(
    matched_markets: list[MatchedMarket],
    min_profit_cents: float | None = None,
) -> list[ArbitrageOpportunity]:
    """Scan all matched markets for arbitrage in both directions.

    Direction A: Buy YES on Kalshi  + Buy NO on Polymarket
    Direction B: Buy YES on Polymarket + Buy NO on Kalshi
    """
    if min_profit_cents is None:
        min_profit_cents = settings.min_profit_cents

    min_profit = min_profit_cents / 100.0
    opportunities: list[ArbitrageOpportunity] = []

    for mm in matched_markets:
        ko = mm.kalshi_outcome
        po = mm.polymarket_outcome
        if ko is None or po is None:
            continue

        # -- Direction A: YES Kalshi + NO Polymarket --
        opp_a = _check_direction(
            mm=mm,
            direction=Direction.KALSHI_YES_POLY_NO,
            yes_outcome=ko,
            no_outcome=po,
            yes_fee_rate=settings.kalshi_fee_rate,
            no_fee_rate=settings.polymarket_fee_rate,
            min_profit=min_profit,
        )
        if opp_a:
            opportunities.append(opp_a)

        # -- Direction B: YES Polymarket + NO Kalshi --
        opp_b = _check_direction(
            mm=mm,
            direction=Direction.POLY_YES_KALSHI_NO,
            yes_outcome=po,
            no_outcome=ko,
            yes_fee_rate=settings.polymarket_fee_rate,
            no_fee_rate=settings.kalshi_fee_rate,
            min_profit=min_profit,
        )
        if opp_b:
            opportunities.append(opp_b)

    opportunities.sort(key=lambda o: o.roi, reverse=True)
    return opportunities


def _check_direction(
    mm: MatchedMarket,
    direction: Direction,
    yes_outcome: Outcome,
    no_outcome: Outcome,
    yes_fee_rate: float,
    no_fee_rate: float,
    min_profit: float,
) -> ArbitrageOpportunity | None:
    """Check one direction for an arb opportunity."""
    # Use ask prices when available (what you'd actually pay), fall back to mid prices
    yes_price = yes_outcome.yes_ask if yes_outcome.yes_ask > 0 else yes_outcome.yes_price
    no_price = no_outcome.no_ask if no_outcome.no_ask > 0 else no_outcome.no_price

    if yes_price <= 0 or no_price <= 0:
        return None

    cost = _effective_cost(yes_price, no_price, yes_fee_rate, no_fee_rate)
    profit = 1.0 - cost

    if profit < min_profit:
        return None

    roi = (profit / cost) * 100.0 if cost > 0 else 0.0
    max_size = min(
        yes_outcome.yes_depth if yes_outcome.yes_depth > 0 else float("inf"),
        no_outcome.no_depth if no_outcome.no_depth > 0 else float("inf"),
    )
    if max_size == float("inf"):
        max_size = 0.0

    return ArbitrageOpportunity(
        matched_market=mm,
        direction=direction,
        cost=round(cost, 4),
        profit=round(profit, 4),
        roi=round(roi, 2),
        max_size=round(max_size, 2),
        timestamp=datetime.now(timezone.utc),
        kalshi_price=yes_price if direction == Direction.KALSHI_YES_POLY_NO else no_price,
        polymarket_price=no_price if direction == Direction.KALSHI_YES_POLY_NO else yes_price,
    )
