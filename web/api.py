from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.scanner import ArbitrageScanner

logger = logging.getLogger(__name__)

scanner = ArbitrageScanner()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await scanner.start()
    yield
    await scanner.stop()


app = FastAPI(title="Polymath Arbitrage Bot", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="web/static"), name="static")


# -- REST Endpoints --


@app.get("/")
async def root():
    with open("web/static/index.html") as f:
        return HTMLResponse(f.read())


@app.get("/api/opportunities")
async def get_opportunities():
    return JSONResponse([o.to_dict() for o in scanner.opportunities])


@app.get("/api/matched-markets")
async def get_matched_markets():
    results = []
    for mm in scanner.matched_markets:
        expiry = mm.kalshi_market.expiration or mm.polymarket_market.expiration
        results.append({
            "kalshi_title": mm.kalshi_market.title,
            "kalshi_ticker": mm.kalshi_market.ticker,
            "kalshi_url": mm.kalshi_market.url,
            "polymarket_title": mm.polymarket_market.title,
            "polymarket_url": mm.polymarket_market.url,
            "similarity": round(mm.similarity_score, 1),
            "expiry": expiry.isoformat() if expiry else None,
            "kalshi_yes": round(mm.kalshi_outcome.yes_price, 4) if mm.kalshi_outcome else 0,
            "kalshi_no": round(mm.kalshi_outcome.no_price, 4) if mm.kalshi_outcome else 0,
            "poly_yes": round(mm.polymarket_outcome.yes_price, 4) if mm.polymarket_outcome else 0,
            "poly_no": round(mm.polymarket_outcome.no_price, 4) if mm.polymarket_outcome else 0,
        })
    return JSONResponse(results)


@app.get("/api/stats")
async def get_stats():
    return JSONResponse({
        "kalshi_markets": scanner.stats.kalshi_markets,
        "polymarket_markets": scanner.stats.polymarket_markets,
        "matched_pairs": scanner.stats.matched_pairs,
        "active_opportunities": scanner.stats.active_opportunities,
        "total_scans": scanner.stats.total_scans,
        "last_scan": scanner.stats.last_scan,
        "is_running": scanner.stats.is_running,
        "scan_interval": scanner.stats.scan_interval,
        "auto_execute": scanner.stats.auto_execute,
        "errors": scanner.stats.errors[-5:],
    })


class SettingsUpdate(BaseModel):
    scan_interval: Optional[int] = None
    min_profit_cents: Optional[float] = None
    match_threshold: Optional[int] = None
    auto_execute: Optional[bool] = None
    max_position_usd: Optional[float] = None


@app.post("/api/settings")
async def update_settings(body: SettingsUpdate):
    scanner.update_settings(
        scan_interval=body.scan_interval,
        min_profit_cents=body.min_profit_cents,
        match_threshold=body.match_threshold,
        auto_execute=body.auto_execute,
        max_position_usd=body.max_position_usd,
    )
    return JSONResponse({"status": "ok"})


# -- WebSocket --


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    logger.info("WebSocket client connected")

    async def send_update(data: dict):
        try:
            await ws.send_json(data)
        except Exception:
            pass

    scanner.register_ws_callback(send_update)
    try:
        # Send current state immediately
        await send_update({
            "type": "scan_update",
            "opportunities": [o.to_dict() for o in scanner.opportunities],
            "stats": {
                "kalshi_markets": scanner.stats.kalshi_markets,
                "polymarket_markets": scanner.stats.polymarket_markets,
                "matched_pairs": scanner.stats.matched_pairs,
                "active_opportunities": scanner.stats.active_opportunities,
                "total_scans": scanner.stats.total_scans,
                "last_scan": scanner.stats.last_scan,
            },
        })
        # Keep connection alive
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    finally:
        scanner.unregister_ws_callback(send_update)
