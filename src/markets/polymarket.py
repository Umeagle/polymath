from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Optional

import httpx

from config import settings
from src.markets.models import Market, Outcome, Platform

logger = logging.getLogger(__name__)


class PolymarketClient:
    """Fetches markets and orderbook data from Polymarket (Gamma + CLOB APIs)."""

    def __init__(self) -> None:
        self.gamma_url = settings.polymarket_gamma_url
        self.clob_url = settings.polymarket_clob_url
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=20.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    # -- Market Discovery (Gamma API) --

    async def fetch_active_markets(self, max_markets: int | None = None) -> list[Market]:
        """Fetch active events from Gamma API (ordered by 24h volume) and flatten into Markets."""
        if max_markets is None:
            max_markets = settings.max_polymarket_markets
        client = await self._client()
        markets: list[Market] = []
        offset = 0
        limit = 100

        while len(markets) < max_markets:
            try:
                resp = await client.get(
                    f"{self.gamma_url}/events",
                    params={
                        "active": "true",
                        "closed": "false",
                        "archived": "false",
                        "limit": limit,
                        "offset": offset,
                        "order": "volume24hr",
                        "ascending": "false",
                    },
                )
                resp.raise_for_status()
                events = resp.json()
            except Exception:
                logger.exception("Failed to fetch Polymarket events (offset=%d)", offset)
                break

            if not events:
                break

            for event in events:
                event_title = event.get("title", "")
                for mkt in event.get("markets", []):
                    outcomes = self._parse_outcomes(mkt)
                    expiration = self._parse_expiration(mkt)
                    markets.append(
                        Market(
                            platform=Platform.POLYMARKET,
                            id=mkt.get("id", ""),
                            title=mkt.get("question", mkt.get("title", "")),
                            event_title=event_title,
                            outcomes=outcomes,
                            expiration=expiration,
                            volume=float(mkt.get("volume", 0) or 0),
                            url=f"https://polymarket.com/event/{event.get('slug', '')}",
                            ticker=mkt.get("conditionId", ""),
                        )
                    )
                    if len(markets) >= max_markets:
                        break
                if len(markets) >= max_markets:
                    break

            if len(events) < limit:
                break
            offset += limit
            await asyncio.sleep(0.1)

        logger.info("Polymarket: fetched %d markets (cap=%d)", len(markets), max_markets)
        return markets

    @staticmethod
    def _parse_expiration(mkt: dict) -> Optional[datetime]:
        for field in ("end_date_iso", "endDate", "endDateIso", "close_time"):
            val = mkt.get(field)
            if val:
                try:
                    return datetime.fromisoformat(str(val).replace("Z", "+00:00"))
                except Exception:
                    continue
        return None

    @staticmethod
    def _parse_outcomes(mkt: dict) -> list[Outcome]:
        """Parse outcome tokens from a Gamma market object."""
        outcomes: list[Outcome] = []
        outcome_names = mkt.get("outcomes", [])
        if isinstance(outcome_names, str):
            import json
            try:
                outcome_names = json.loads(outcome_names)
            except Exception:
                outcome_names = []

        outcome_prices = mkt.get("outcomePrices", [])
        if isinstance(outcome_prices, str):
            import json
            try:
                outcome_prices = json.loads(outcome_prices)
            except Exception:
                outcome_prices = []

        clob_token_ids = mkt.get("clobTokenIds", [])
        if isinstance(clob_token_ids, str):
            import json
            try:
                clob_token_ids = json.loads(clob_token_ids)
            except Exception:
                clob_token_ids = []

        for i, name in enumerate(outcome_names):
            price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
            token_id = clob_token_ids[i] if i < len(clob_token_ids) else ""
            outcomes.append(
                Outcome(
                    name=str(name),
                    yes_price=price,
                    no_price=round(1.0 - price, 4) if price else 0.0,
                    token_id=str(token_id),
                )
            )
        return outcomes

    # -- Orderbook (CLOB API) --

    async def fetch_orderbook(self, token_id: str) -> dict:
        """Fetch the CLOB orderbook for a given token_id. Returns raw book dict."""
        client = await self._client()
        try:
            resp = await client.get(
                f"{self.clob_url}/book",
                params={"token_id": token_id},
            )
            if resp.status_code == 404:
                logger.debug("No orderbook for Polymarket token %s", token_id[:20])
                return {}
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("Failed to fetch Polymarket orderbook for %s", token_id[:20])
            return {}

    async def fetch_price(self, token_id: str) -> dict:
        """Fetch the mid-market price for a token."""
        client = await self._client()
        try:
            resp = await client.get(
                f"{self.clob_url}/price",
                params={"token_id": token_id, "side": "buy"},
            )
            if resp.status_code == 404:
                return {}
            resp.raise_for_status()
            return resp.json()
        except Exception:
            logger.warning("Failed to fetch Polymarket price for %s", token_id[:20])
            return {}

    async def enrich_outcomes_with_orderbook(self, market: Market) -> Market:
        """Fetch orderbook for each outcome and populate ask/bid/depth fields."""
        for outcome in market.outcomes:
            if not outcome.token_id:
                continue
            book = await self.fetch_orderbook(outcome.token_id)
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if asks:
                best_ask = min(asks, key=lambda o: float(o.get("price", 999)))
                outcome.yes_ask = float(best_ask.get("price", 0))
                outcome.yes_depth = float(best_ask.get("size", 0))

            if bids:
                best_bid = max(bids, key=lambda o: float(o.get("price", 0)))
                outcome.yes_bid = float(best_bid.get("price", 0))

            outcome.no_ask = round(1.0 - outcome.yes_bid, 4) if outcome.yes_bid else 0.0
            outcome.no_bid = round(1.0 - outcome.yes_ask, 4) if outcome.yes_ask else 0.0
            outcome.no_depth = outcome.yes_depth

            await asyncio.sleep(0.05)

        return market
