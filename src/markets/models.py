from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    POLYMARKET = "polymarket"
    KALSHI = "kalshi"


@dataclass
class Outcome:
    name: str
    yes_price: float = 0.0
    no_price: float = 0.0
    token_id: str = ""
    yes_ask: float = 0.0
    no_ask: float = 0.0
    yes_bid: float = 0.0
    no_bid: float = 0.0
    yes_depth: float = 0.0
    no_depth: float = 0.0


@dataclass
class Market:
    platform: Platform
    id: str
    title: str
    event_title: str = ""
    outcomes: list[Outcome] = field(default_factory=list)
    expiration: Optional[datetime] = None
    volume: float = 0.0
    url: str = ""
    ticker: str = ""


@dataclass
class MatchedMarket:
    kalshi_market: Market
    polymarket_market: Market
    similarity_score: float = 0.0
    kalshi_outcome: Optional[Outcome] = None
    polymarket_outcome: Optional[Outcome] = None


class Direction(str, Enum):
    KALSHI_YES_POLY_NO = "YES on Kalshi + NO on Polymarket"
    POLY_YES_KALSHI_NO = "YES on Polymarket + NO on Kalshi"


@dataclass
class ArbitrageOpportunity:
    matched_market: MatchedMarket
    direction: Direction
    cost: float
    profit: float
    roi: float
    max_size: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)
    kalshi_price: float = 0.0
    polymarket_price: float = 0.0

    def to_dict(self) -> dict:
        km = self.matched_market.kalshi_market
        pm = self.matched_market.polymarket_market
        expiry = km.expiration or pm.expiration
        return {
            "kalshi_title": km.title,
            "polymarket_title": pm.title,
            "kalshi_ticker": km.ticker,
            "similarity": round(self.matched_market.similarity_score, 1),
            "direction": self.direction.value,
            "kalshi_price": round(self.kalshi_price, 4),
            "polymarket_price": round(self.polymarket_price, 4),
            "cost": round(self.cost, 4),
            "profit": round(self.profit, 4),
            "roi": round(self.roi, 2),
            "max_size": round(self.max_size, 2),
            "timestamp": self.timestamp.isoformat(),
            "expiry": expiry.isoformat() if expiry else None,
            "kalshi_url": km.url,
            "polymarket_url": pm.url,
        }
