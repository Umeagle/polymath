from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from config import settings
from src.markets.models import Market, Outcome, Platform

logger = logging.getLogger(__name__)

# All major Kalshi series worth scanning for cross-platform arbitrage.
# Crypto, sports, economics, weather, politics -- everything.
SERIES_TICKERS = [
    # Crypto
    "KXBTC", "KXBTCD", "KXETH", "KXETHD", "KXXRP", "KXXRPD",
    "KXDOGE", "KXDOGED", "KXSOLD", "KXSOLE",
    # Stock indices
    "KXINX",
    # Sports - NBA
    "KXNBA", "KXNBASPREAD", "KXNBATOTAL", "KXNBAPTS",
    "KXNBAREB", "KXNBAAST", "KXNBAWINS",
    "KXMVENBASINGLEGAME",
    # Sports - NCAA basketball
    "KXNCAAMBGAME", "KXNCAAMBTOTAL", "KXNCAAMBSPREAD",
    "KXNCAAMB1HSPREAD", "KXNCAAMB1HTOTAL", "KXNCAAMB1HWINNER",
    "KXNCAAWBGAME",
    # Sports - NFL / NCAA football
    "KXNEXTTEAMNFL", "KXNCAAF", "KXNFLDRAFTPICK",
    # Sports - NHL, MLB, golf, other
    "KXNHL", "KXNHLTOTAL", "KXMLB", "KXPGATOUR", "KXPGATOP5",
    "KXPGATOP10", "KXPGATOP20", "KXPGAMAKECUT",
    "KXWCGAME", "KXWCROUND", "KXMARMADROUND", "KXMAKEMARMAD",
    "KXDPWORLDTOUR", "KXDPWORLDTOURR1LEAD",
    # Economics
    "KXFEDDECISION", "KXFED", "KXCPI", "KXGDP", "KXGDPNOM",
    "KXPAYROLLS", "KXECONSTATCPIYOY", "KXECONSTATCORECPIYOY",
    "KXECONSTATU3",
    # Weather
    "KXHIGHNY", "KXHIGHCHI", "KXHIGHMIA",
    # Politics
    "KXHOUSERACE", "KXTXPRIMARY",
    # Entertainment
    "KXALBUMSALES", "KXALBUMRELEASE", "KX10SONG",
]


class KalshiClient:
    """Fetches markets and orderbook data from Kalshi's REST API v2."""

    def __init__(self) -> None:
        self.base_url = settings.kalshi_api_url
        self._http: Optional[httpx.AsyncClient] = None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=20.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def _get_with_retry(self, url: str, params: dict | None = None) -> httpx.Response:
        """GET with automatic retry on 429 rate-limit responses."""
        client = await self._client()
        for attempt in range(5):
            resp = await client.get(url, params=params)
            if resp.status_code == 429:
                wait = 1.5 * (attempt + 1)
                logger.warning("Kalshi 429 rate-limited, waiting %.1fs", wait)
                await asyncio.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    # -- Series-targeted fetching --

    async def _fetch_series(self, series_ticker: str, max_per_series: int = 500) -> list[Market]:
        """Fetch open markets for one series ticker."""
        markets: list[Market] = []
        cursor: Optional[str] = None
        limit = 200

        while len(markets) < max_per_series:
            params: dict = {"series_ticker": series_ticker, "status": "open", "limit": limit}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = await self._get_with_retry(f"{self.base_url}/markets", params=params)
                data = resp.json()
            except Exception:
                logger.warning("Failed to fetch Kalshi series %s", series_ticker)
                break

            for mkt in data.get("markets", []):
                market = self._parse_market(mkt, event_title=series_ticker)
                if market:
                    markets.append(market)

            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                break
            await asyncio.sleep(0.1)

        return markets

    # -- Events-based broad discovery --

    async def _fetch_events(self, max_events: int = 500) -> list[dict]:
        """Fetch all active events with nested markets."""
        events: list[dict] = []
        cursor: Optional[str] = None
        limit = 100
        pages_fetched = 0

        while len(events) < max_events and pages_fetched < 30:
            params: dict = {"status": "open", "limit": limit, "with_nested_markets": "true"}
            if cursor:
                params["cursor"] = cursor
            try:
                resp = await self._get_with_retry(f"{self.base_url}/events", params=params)
                data = resp.json()
            except Exception:
                logger.exception("Failed to fetch Kalshi events")
                break

            for event in data.get("events", []):
                events.append(event)
                if len(events) >= max_events:
                    break

            cursor = data.get("cursor")
            if not cursor or not data.get("events"):
                break
            pages_fetched += 1
            await asyncio.sleep(0.15)

        return events

    # -- Main entry point --

    async def fetch_active_markets(self, max_markets: int | None = None) -> list[Market]:
        """Fetch markets from targeted series + events API in parallel."""
        if max_markets is None:
            max_markets = settings.max_kalshi_markets

        # Fire off series fetches (batched to avoid rate-limit storms)
        batch_size = 8
        all_series_markets: list[Market] = []

        for i in range(0, len(SERIES_TICKERS), batch_size):
            batch = SERIES_TICKERS[i:i + batch_size]
            results = await asyncio.gather(
                *[self._fetch_series(s) for s in batch],
                return_exceptions=True,
            )
            for j, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning("Series %s failed: %s", batch[j], result)
                    continue
                all_series_markets.extend(result)
            if i + batch_size < len(SERIES_TICKERS):
                await asyncio.sleep(0.3)

        # Also fetch from events API for broad coverage
        events = await self._fetch_events(max_events=500)

        # Merge, dedup by ticker
        seen_ids: set[str] = set()
        markets: list[Market] = []

        for m in all_series_markets:
            if m.id not in seen_ids:
                seen_ids.add(m.id)
                markets.append(m)

        events_added = 0
        for event in events:
            event_title = event.get("title", "")
            for mkt in event.get("markets", []):
                market = self._parse_market(mkt, event_title=event_title)
                if market and market.id not in seen_ids:
                    seen_ids.add(market.id)
                    markets.append(market)
                    events_added += 1
                if len(markets) >= max_markets:
                    break
            if len(markets) >= max_markets:
                break

        logger.info(
            "Kalshi: %d total markets (%d from %d series, %d from events)",
            len(markets), len(markets) - events_added,
            len(SERIES_TICKERS), events_added,
        )
        return markets

    @staticmethod
    def _parse_market(mkt: dict, event_title: str = "") -> Optional[Market]:
        ticker = mkt.get("ticker", "")
        title = mkt.get("title", "")
        if not ticker or not title:
            return None

        yes_price = float(mkt.get("yes_price", 0) or 0)
        no_price = float(mkt.get("no_price", 0) or 0)
        if yes_price == 0 and no_price == 0:
            last_price = float(mkt.get("last_price", 0) or 0)
            if last_price > 0:
                yes_price = last_price / 100.0 if last_price > 1 else last_price
                no_price = round(1.0 - yes_price, 4)

        if yes_price > 1:
            yes_price = yes_price / 100.0
        if no_price > 1:
            no_price = no_price / 100.0

        outcome = Outcome(
            name=title,
            yes_price=yes_price,
            no_price=no_price,
            token_id=ticker,
        )

        expiration = None
        exp_str = mkt.get("expiration_time") or mkt.get("close_time")
        if exp_str:
            try:
                expiration = datetime.fromisoformat(exp_str.replace("Z", "+00:00"))
            except Exception:
                pass

        volume = float(mkt.get("volume", 0) or 0)
        subtitle = mkt.get("subtitle", "")
        event_ticker_raw = mkt.get("event_ticker", "")
        # Kalshi URLs: /markets/{series}/{event_ticker} links to the specific event.
        series = mkt.get("series_ticker", "")
        if not series and event_ticker_raw:
            series = event_ticker_raw.split("-")[0]
        if not series:
            series = ticker.split("-")[0]
        event_slug = event_ticker_raw.lower() if event_ticker_raw else ticker.lower()
        url = f"https://kalshi.com/markets/{series.lower()}/{event_slug}"

        return Market(
            platform=Platform.KALSHI,
            id=ticker,
            title=title,
            event_title=event_title or subtitle or event_ticker_raw,
            outcomes=[outcome],
            expiration=expiration,
            volume=volume,
            url=url,
            ticker=ticker,
        )

    # -- Orderbook --

    async def fetch_orderbook(self, ticker: str) -> dict:
        try:
            resp = await self._get_with_retry(f"{self.base_url}/markets/{ticker}/orderbook")
            return resp.json()
        except Exception:
            logger.exception("Failed to fetch Kalshi orderbook for %s", ticker)
            return {}

    async def enrich_outcomes_with_orderbook(self, market: Market) -> Market:
        """Populate ask/bid/depth from orderbook data."""
        book_data = await self.fetch_orderbook(market.ticker)
        orderbook = book_data.get("orderbook", {})

        yes_bids = orderbook.get("yes", [])
        no_bids = orderbook.get("no", [])

        for outcome in market.outcomes:
            if yes_bids:
                best_yes_bid_entry = yes_bids[0]
                price_raw = best_yes_bid_entry[0] if isinstance(best_yes_bid_entry, list) else best_yes_bid_entry
                price_val = float(price_raw)
                if price_val > 1:
                    price_val /= 100.0
                outcome.yes_bid = price_val
                outcome.yes_depth = float(
                    best_yes_bid_entry[1] if isinstance(best_yes_bid_entry, list) else 0
                )

            if no_bids:
                best_no_bid_entry = no_bids[0]
                price_raw = best_no_bid_entry[0] if isinstance(best_no_bid_entry, list) else best_no_bid_entry
                price_val = float(price_raw)
                if price_val > 1:
                    price_val /= 100.0
                outcome.no_bid = price_val
                outcome.no_depth = float(
                    best_no_bid_entry[1] if isinstance(best_no_bid_entry, list) else 0
                )

            if outcome.no_bid > 0:
                outcome.yes_ask = round(1.0 - outcome.no_bid, 4)
            if outcome.yes_bid > 0:
                outcome.no_ask = round(1.0 - outcome.yes_bid, 4)

        return market
