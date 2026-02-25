from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from dataclasses import dataclass, field

from config import settings
from src.markets.models import ArbitrageOpportunity, Direction

logger = logging.getLogger(__name__)


@dataclass
class ExecutionRecord:
    opportunity: ArbitrageOpportunity
    executed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    success: bool = False
    error: str = ""
    pnl: float = 0.0


class TradeExecutor:
    """Executes arbitrage trades on Polymarket and Kalshi with safety guardrails."""

    def __init__(self) -> None:
        self.enabled: bool = settings.auto_execute
        self.max_position_usd: float = settings.max_position_size_usd
        self.max_daily_loss_usd: float = settings.max_daily_loss_usd
        self.min_profit_cents: float = settings.min_profit_cents
        self.cooldown_seconds: float = 5.0

        self._daily_pnl: float = 0.0
        self._daily_reset_date: str = ""
        self._last_execution: datetime | None = None
        self._execution_log: list[ExecutionRecord] = []

        self._poly_client = None
        self._kalshi_client = None

    @property
    def execution_log(self) -> list[ExecutionRecord]:
        return list(self._execution_log)

    def _reset_daily_if_needed(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_reset_date != today:
            self._daily_pnl = 0.0
            self._daily_reset_date = today

    def _check_guardrails(self, opp: ArbitrageOpportunity) -> str | None:
        """Return an error message if guardrails prevent execution, else None."""
        if not self.enabled:
            return "Auto-execution is disabled"

        self._reset_daily_if_needed()

        if self._daily_pnl < -self.max_daily_loss_usd:
            return f"Daily loss limit reached (${self._daily_pnl:.2f})"

        if opp.profit * 100 < self.min_profit_cents:
            return f"Profit {opp.profit*100:.1f}¢ below minimum {self.min_profit_cents}¢"

        if self._last_execution:
            elapsed = (datetime.now(timezone.utc) - self._last_execution).total_seconds()
            if elapsed < self.cooldown_seconds:
                return f"Cooldown active ({elapsed:.1f}s / {self.cooldown_seconds}s)"

        position_size = min(opp.max_size, self.max_position_usd) if opp.max_size > 0 else self.max_position_usd
        if position_size <= 0:
            return "No executable size available"

        return None

    async def execute(self, opp: ArbitrageOpportunity) -> ExecutionRecord:
        """Attempt to execute an arbitrage opportunity on both platforms."""
        guard_error = self._check_guardrails(opp)
        if guard_error:
            record = ExecutionRecord(opportunity=opp, error=guard_error)
            self._execution_log.append(record)
            logger.info("Execution blocked: %s", guard_error)
            return record

        position_size = min(opp.max_size, self.max_position_usd) if opp.max_size > 0 else self.max_position_usd

        logger.info(
            "Executing arb: %s | cost=%.4f profit=%.4f size=%.2f",
            opp.direction.value,
            opp.cost,
            opp.profit,
            position_size,
        )

        try:
            if opp.direction == Direction.KALSHI_YES_POLY_NO:
                results = await asyncio.gather(
                    self._buy_kalshi_yes(opp, position_size),
                    self._buy_polymarket_no(opp, position_size),
                    return_exceptions=True,
                )
            else:
                results = await asyncio.gather(
                    self._buy_polymarket_yes(opp, position_size),
                    self._buy_kalshi_no(opp, position_size),
                    return_exceptions=True,
                )

            errors = [r for r in results if isinstance(r, Exception)]
            if errors:
                raise errors[0]

            estimated_pnl = opp.profit * position_size
            self._daily_pnl += estimated_pnl
            self._last_execution = datetime.now(timezone.utc)

            record = ExecutionRecord(
                opportunity=opp,
                success=True,
                pnl=estimated_pnl,
            )
            self._execution_log.append(record)
            logger.info("Execution succeeded: estimated PnL=$%.4f", estimated_pnl)
            return record

        except Exception as exc:
            record = ExecutionRecord(opportunity=opp, error=str(exc))
            self._execution_log.append(record)
            logger.exception("Execution failed")
            return record

    # -- Platform-specific order methods (stubs for SDK integration) --

    async def _buy_kalshi_yes(self, opp: ArbitrageOpportunity, size: float) -> None:
        """Place a YES buy order on Kalshi. Requires API credentials."""
        ticker = opp.matched_market.kalshi_market.ticker
        price = opp.kalshi_price
        logger.info("[KALSHI] BUY YES %s @ %.4f x %.2f", ticker, price, size)

        if not settings.kalshi_api_key_id:
            logger.warning("[KALSHI] No API key configured -- dry run only")
            return

        # TODO: Integrate kalshi-python SDK when credentials are available
        # from kalshi_python import KalshiClient
        # client.create_order(ticker=ticker, side="yes", action="buy", count=int(size), ...)

    async def _buy_kalshi_no(self, opp: ArbitrageOpportunity, size: float) -> None:
        ticker = opp.matched_market.kalshi_market.ticker
        price = opp.kalshi_price
        logger.info("[KALSHI] BUY NO %s @ %.4f x %.2f", ticker, price, size)

        if not settings.kalshi_api_key_id:
            logger.warning("[KALSHI] No API key configured -- dry run only")
            return

    async def _buy_polymarket_yes(self, opp: ArbitrageOpportunity, size: float) -> None:
        po = opp.matched_market.polymarket_outcome
        token_id = po.token_id if po else ""
        price = opp.polymarket_price
        logger.info("[POLYMARKET] BUY YES token=%s @ %.4f x %.2f", token_id[:16], price, size)

        if not settings.polymarket_private_key:
            logger.warning("[POLYMARKET] No private key configured -- dry run only")
            return

        # TODO: Integrate py-clob-client when credentials are available
        # from py_clob_client.client import ClobClient
        # client.create_and_post_order(OrderArgs(token_id=token_id, price=price, size=size, side=BUY))

    async def _buy_polymarket_no(self, opp: ArbitrageOpportunity, size: float) -> None:
        po = opp.matched_market.polymarket_outcome
        # Buying NO on Polymarket = buying the second outcome token (index 1)
        pm = opp.matched_market.polymarket_market
        no_token_id = ""
        if len(pm.outcomes) > 1:
            no_token_id = pm.outcomes[1].token_id
        elif po:
            no_token_id = po.token_id

        price = opp.polymarket_price
        logger.info("[POLYMARKET] BUY NO token=%s @ %.4f x %.2f", no_token_id[:16], price, size)

        if not settings.polymarket_private_key:
            logger.warning("[POLYMARKET] No private key configured -- dry run only")
            return
