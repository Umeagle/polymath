"""Quick integration test: fetch from both APIs, match, detect arbitrage."""
import asyncio
import logging
import sys

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

async def main():
    from src.markets.polymarket import PolymarketClient
    from src.markets.kalshi import KalshiClient
    from src.matching.matcher import MarketMatcher
    from src.arbitrage.detector import detect_opportunities

    poly = PolymarketClient()
    kalshi = KalshiClient()
    matcher = MarketMatcher()

    print("\n=== Fetching markets from both platforms ===\n")
    kalshi_markets, poly_markets = await asyncio.gather(
        kalshi.fetch_active_markets(),
        poly.fetch_active_markets(),
    )
    print(f"  Kalshi:     {len(kalshi_markets)} markets")
    print(f"  Polymarket: {len(poly_markets)} markets")

    if not kalshi_markets or not poly_markets:
        print("\nERROR: Could not fetch markets from one or both platforms.")
        await poly.close()
        await kalshi.close()
        return 1

    print("\n=== Fuzzy matching markets ===\n")
    matched = matcher.match(kalshi_markets, poly_markets)
    print(f"  Matched pairs: {len(matched)}")

    for mm in matched[:10]:
        print(f"    [{mm.similarity_score:.0f}%] K: {mm.kalshi_market.title[:60]}")
        print(f"           P: {mm.polymarket_market.title[:60]}\n")

    if not matched:
        print("\nNo matches found. Pipeline works but markets may not overlap.")
        await poly.close()
        await kalshi.close()
        return 0

    print("=== Enriching orderbooks (first 5 pairs) ===\n")
    for mm in matched[:5]:
        await kalshi.enrich_outcomes_with_orderbook(mm.kalshi_market)
        await poly.enrich_outcomes_with_orderbook(mm.polymarket_market)
        mm.kalshi_outcome = mm.kalshi_market.outcomes[0] if mm.kalshi_market.outcomes else None
        mm.polymarket_outcome = mm.polymarket_market.outcomes[0] if mm.polymarket_market.outcomes else None

    print("=== Detecting arbitrage opportunities ===\n")
    opps = detect_opportunities(matched[:5], min_profit_cents=0.5)
    print(f"  Opportunities found: {len(opps)}")
    for o in opps[:5]:
        print(f"    {o.direction.value}")
        print(f"      K: {o.matched_market.kalshi_market.title[:50]}")
        print(f"      P: {o.matched_market.polymarket_market.title[:50]}")
        print(f"      Cost={o.cost:.4f}  Profit={o.profit:.4f}  ROI={o.roi:.1f}%\n")

    if not opps:
        print("  (No arb detected at current prices -- this is normal)")

    print("=== Pipeline test PASSED ===\n")
    await poly.close()
    await kalshi.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
