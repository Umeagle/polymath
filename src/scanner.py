from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from config import settings
from src.markets.polymarket import PolymarketClient
from src.markets.kalshi import KalshiClient
from src.markets.models import ArbitrageOpportunity, MatchedMarket, Market
from src.matching.matcher import MarketMatcher
from src.arbitrage.detector import detect_opportunities
from src.arbitrage.executor import TradeExecutor

logger = logging.getLogger(__name__)


@dataclass
class ScannerStats:
    kalshi_markets: int = 0
    polymarket_markets: int = 0
    matched_pairs: int = 0
    active_opportunities: int = 0
    total_scans: int = 0
    last_scan: str = ""
    is_running: bool = False
    scan_interval: int = 5
    auto_execute: bool = False
    errors: list[str] = field(default_factory=list)


class ArbitrageScanner:
    """Orchestrates the fetch -> match -> detect -> (execute) loop."""

    def __init__(self) -> None:
        self.poly_client = PolymarketClient()
        self.kalshi_client = KalshiClient()
        self.matcher = MarketMatcher()
        self.executor = TradeExecutor()

        self.stats = ScannerStats(
            scan_interval=settings.scan_interval_seconds,
            auto_execute=settings.auto_execute,
        )

        self.opportunities: list[ArbitrageOpportunity] = []
        self.matched_markets: list[MatchedMarket] = []
        self.kalshi_markets: list[Market] = []
        self.polymarket_markets: list[Market] = []

        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._ws_broadcast: list = []

    def register_ws_callback(self, callback) -> None:
        self._ws_broadcast.append(callback)

    def unregister_ws_callback(self, callback) -> None:
        self._ws_broadcast = [cb for cb in self._ws_broadcast if cb is not callback]

    async def start(self) -> None:
        if self._task and not self._task.done():
            logger.warning("Scanner already running")
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run_loop())
        self.stats.is_running = True
        logger.info("Scanner started (interval=%ds)", settings.scan_interval_seconds)

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self.stats.is_running = False
        await self.poly_client.close()
        await self.kalshi_client.close()
        logger.info("Scanner stopped")

    async def _run_loop(self) -> None:
        backoff = 1.0
        max_backoff = 60.0

        while not self._stop_event.is_set():
            try:
                await self._scan_once()
                backoff = 1.0
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error_msg = f"Scan error: {exc}"
                logger.exception(error_msg)
                self.stats.errors = (self.stats.errors + [error_msg])[-20:]
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, max_backoff)
                continue

            try:
                await asyncio.wait_for(
                    self._stop_event.wait(),
                    timeout=settings.scan_interval_seconds,
                )
                break
            except asyncio.TimeoutError:
                pass

    async def _scan_once(self) -> None:
        logger.debug("Starting scan #%d", self.stats.total_scans + 1)

        # Fetch markets from both platforms concurrently
        kalshi_markets, poly_markets = await asyncio.gather(
            self.kalshi_client.fetch_active_markets(),
            self.poly_client.fetch_active_markets(),
        )

        self.kalshi_markets = kalshi_markets
        self.polymarket_markets = poly_markets
        self.stats.kalshi_markets = len(kalshi_markets)
        self.stats.polymarket_markets = len(poly_markets)

        # Match markets (CPU-heavy, run in thread to keep event loop responsive)
        self.matched_markets = await asyncio.to_thread(
            self.matcher.match, kalshi_markets, poly_markets,
        )
        self.stats.matched_pairs = len(self.matched_markets)

        # Enrich matched markets with orderbook data (rate-limited)
        await self._enrich_orderbooks()

        # Detect arbitrage opportunities
        self.opportunities = detect_opportunities(
            self.matched_markets,
            min_profit_cents=settings.min_profit_cents,
        )
        self.stats.active_opportunities = len(self.opportunities)
        self.stats.total_scans += 1
        self.stats.last_scan = datetime.now(timezone.utc).isoformat()

        logger.info(
            "Scan #%d complete: %d Kalshi, %d Poly, %d matched, %d opportunities",
            self.stats.total_scans,
            len(kalshi_markets),
            len(poly_markets),
            len(self.matched_markets),
            len(self.opportunities),
        )

        # Broadcast to WebSocket clients
        await self._broadcast()

        # Auto-execute if enabled
        if self.executor.enabled and self.opportunities:
            best = self.opportunities[0]
            await self.executor.execute(best)

    async def _enrich_orderbooks(self) -> None:
        """Fetch orderbook data for matched markets, batched to respect rate limits."""
        tasks = []
        for mm in self.matched_markets:
            tasks.append(self.kalshi_client.enrich_outcomes_with_orderbook(mm.kalshi_market))
            # For Polymarket, only enrich the specific outcome token
            if mm.polymarket_outcome and mm.polymarket_outcome.token_id:
                tasks.append(
                    self.poly_client.enrich_outcomes_with_orderbook(mm.polymarket_market)
                )

        # Process in batches to respect rate limits
        batch_size = 8
        for i in range(0, len(tasks), batch_size):
            batch = tasks[i : i + batch_size]
            await asyncio.gather(*batch, return_exceptions=True)
            if i + batch_size < len(tasks):
                await asyncio.sleep(0.2)

        # Re-link enriched outcomes back into MatchedMarket
        for mm in self.matched_markets:
            if mm.kalshi_market.outcomes:
                mm.kalshi_outcome = mm.kalshi_market.outcomes[0]
            if mm.polymarket_market.outcomes:
                mm.polymarket_outcome = mm.polymarket_market.outcomes[0]

    async def _broadcast(self) -> None:
        if not self._ws_broadcast:
            return
        data = {
            "type": "scan_update",
            "opportunities": [o.to_dict() for o in self.opportunities],
            "stats": {
                "kalshi_markets": self.stats.kalshi_markets,
                "polymarket_markets": self.stats.polymarket_markets,
                "matched_pairs": self.stats.matched_pairs,
                "active_opportunities": self.stats.active_opportunities,
                "total_scans": self.stats.total_scans,
                "last_scan": self.stats.last_scan,
            },
        }
        dead = []
        for cb in self._ws_broadcast:
            try:
                await cb(data)
            except Exception:
                dead.append(cb)
        for cb in dead:
            self._ws_broadcast.remove(cb)

    def update_settings(
        self,
        scan_interval: int | None = None,
        min_profit_cents: float | None = None,
        match_threshold: int | None = None,
        auto_execute: bool | None = None,
        max_position_usd: float | None = None,
    ) -> None:
        if scan_interval is not None:
            settings.scan_interval_seconds = scan_interval
            self.stats.scan_interval = scan_interval
        if min_profit_cents is not None:
            settings.min_profit_cents = min_profit_cents
            self.executor.min_profit_cents = min_profit_cents
        if match_threshold is not None:
            settings.match_similarity_threshold = match_threshold
            self.matcher.threshold = match_threshold
            self.matcher.clear_cache()
        if auto_execute is not None:
            settings.auto_execute = auto_execute
            self.executor.enabled = auto_execute
            self.stats.auto_execute = auto_execute
        if max_position_usd is not None:
            settings.max_position_size_usd = max_position_usd
            self.executor.max_position_usd = max_position_usd
