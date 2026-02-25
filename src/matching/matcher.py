from __future__ import annotations

import json
import logging
import os
import re

from rapidfuzz import fuzz, process

from config import settings
from src.markets.models import Market, MatchedMarket

logger = logging.getLogger(__name__)

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "market_overrides.json")


def _normalize(text: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


class MarketMatcher:
    """Matches markets across Kalshi and Polymarket using brute-force fuzzy matching."""

    def __init__(self, threshold: int | None = None) -> None:
        self.threshold = threshold or settings.match_similarity_threshold
        self._cache: dict[str, MatchedMarket] = {}
        self._excluded: set[str] = set()
        self._manual_overrides: dict[str, str] = {}
        self._load_overrides()

    def _load_overrides(self) -> None:
        if not os.path.exists(OVERRIDES_PATH):
            return
        try:
            with open(OVERRIDES_PATH) as f:
                data = json.load(f)
            self._manual_overrides = data.get("overrides", {})
            self._excluded = set(data.get("excluded", []))
            logger.info(
                "Loaded %d overrides, %d exclusions",
                len(self._manual_overrides),
                len(self._excluded),
            )
        except Exception:
            logger.exception("Failed to load market overrides")

    def match(
        self,
        kalshi_markets: list[Market],
        polymarket_markets: list[Market],
    ) -> list[MatchedMarket]:
        """Match every Kalshi market against every Polymarket market via fuzzy title similarity."""
        if not kalshi_markets or not polymarket_markets:
            return []

        # Process manual overrides first
        overridden_kalshi: set[str] = set()
        overridden_poly: set[str] = set()
        override_matches: list[MatchedMarket] = []

        poly_by_id = {pm.id: pm for pm in polymarket_markets}
        for km in kalshi_markets:
            if km.id in self._manual_overrides:
                target_id = self._manual_overrides[km.id]
                pm = poly_by_id.get(target_id)
                if pm:
                    m = self._build_match(km, pm, 100.0)
                    override_matches.append(m)
                    self._cache[km.id] = m
                    overridden_kalshi.add(km.id)
                    overridden_poly.add(pm.id)

        # Build the full list of normalized Polymarket titles for rapidfuzz
        poly_list = [pm for pm in polymarket_markets if pm.id not in overridden_poly]
        poly_titles = [_normalize(pm.title) for pm in poly_list]

        if not poly_titles:
            return override_matches

        # Fuzzy match each Kalshi market against all Polymarket titles
        # key: (kalshi_id, poly_id) -> (score, MatchedMarket)
        best_matches: dict[tuple[str, str], tuple[float, MatchedMarket]] = {}

        for km in kalshi_markets:
            if km.id in overridden_kalshi or km.id in self._excluded:
                continue

            if km.id in self._cache:
                cached = self._cache[km.id]
                pair_key = (km.id, cached.polymarket_market.id)
                if pair_key not in best_matches or cached.similarity_score > best_matches[pair_key][0]:
                    best_matches[pair_key] = (cached.similarity_score, cached)
                continue

            norm_title = _normalize(km.title)
            result = process.extractOne(
                norm_title,
                poly_titles,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=self.threshold,
            )

            if result is None:
                continue

            _, score, idx = result
            pm = poly_list[idx]

            pair_key = (km.id, pm.id)
            if pair_key not in best_matches or score > best_matches[pair_key][0]:
                m = self._build_match(km, pm, score)
                best_matches[pair_key] = (score, m)
                self._cache[km.id] = m

        # Deduplicate: each Kalshi market -> best Polymarket match
        kalshi_best: dict[str, tuple[float, MatchedMarket]] = {}
        for (kid, pid), (score, m) in best_matches.items():
            if kid not in kalshi_best or score > kalshi_best[kid][0]:
                kalshi_best[kid] = (score, m)

        # Also deduplicate per Polymarket market (keep highest score)
        poly_best: dict[str, tuple[float, MatchedMarket]] = {}
        for kid, (score, m) in kalshi_best.items():
            pid = m.polymarket_market.id
            if pid not in poly_best or score > poly_best[pid][0]:
                poly_best[pid] = (score, m)

        matched = override_matches + [m for _, m in poly_best.values()]
        matched.sort(key=lambda m: m.similarity_score, reverse=True)

        logger.info("Matched %d market pairs (threshold=%d%%)", len(matched), self.threshold)
        return matched

    @staticmethod
    def _build_match(km: Market, pm: Market, score: float) -> MatchedMarket:
        kalshi_outcome = km.outcomes[0] if km.outcomes else None
        poly_outcome = pm.outcomes[0] if pm.outcomes else None
        return MatchedMarket(
            kalshi_market=km,
            polymarket_market=pm,
            similarity_score=score,
            kalshi_outcome=kalshi_outcome,
            polymarket_outcome=poly_outcome,
        )

    def clear_cache(self) -> None:
        self._cache.clear()
