"""Fetch -> match -> screen -> load books -> evaluate -> rank."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.http import Fetcher
from polyarb.kalshi import KalshiClient
from polyarb.matching import Match, match_politics, match_sports
from polyarb.polymarket import PolymarketClient
from polyarb.strategy import Evaluator, Suggestion

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    matches: list[Match] = field(default_factory=list)
    suggestions: list[Suggestion] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def scan(cfg: Config, fetcher: Fetcher, sports: bool = True, politics: bool = True,
         now: datetime | None = None) -> ScanResult:
    now = now or datetime.now(timezone.utc)
    kalshi, poly = KalshiClient(cfg, fetcher), PolymarketClient(cfg, fetcher)
    result = ScanResult()

    if sports:
        for league in cfg.leagues:
            try:
                ke, pe = kalshi.sports_events(league), poly.sports_events(league)
            except Exception as exc:  # noqa: BLE001 - one league failing shouldn't kill the scan
                result.errors.append(f"{league}: {exc}")
                continue
            result.matches += match_sports(ke, pe, cfg.min_match_score)
    if politics:
        try:
            ke, pe = kalshi.politics_events(), poly.politics_events()
            result.matches += match_politics(ke, pe, cfg.min_match_score, cfg.force_pairs, cfg.block_pairs)
        except Exception as exc:  # noqa: BLE001
            result.errors.append(f"politics: {exc}")

    evaluator = Evaluator(cfg, now)
    for match in result.matches:
        for pair in match.pairs:
            if not evaluator.screen(pair):
                continue
            if cfg.fetch_books:
                for client, quote in ((kalshi, pair.kalshi), (poly, pair.poly)):
                    try:
                        client.load_book(quote)
                    except Exception as exc:  # noqa: BLE001 - fall back to top-of-book
                        log.warning("book fetch failed for %s %s: %s", quote.venue, quote.market_id, exc)
            result.suggestions += evaluator.evaluate(match, pair)

    result.suggestions = dedupe(result.suggestions)
    result.suggestions.sort(key=lambda s: (s.kind != "hedged_arb", not s.sized_from_book, -s.score()))
    return result


def dedupe(suggestions: list[Suggestion]) -> list[Suggestion]:
    """One suggestion per (kind, Polymarket token).

    In a two-team game "KC YES on Kalshi + Bills on Polymarket" and "BUF NO on
    Kalshi + Bills on Polymarket" compete for the same Polymarket liquidity;
    keep whichever nets more, preferring ones sized from real order books.
    """
    best: dict[tuple[str, str], Suggestion] = {}
    for s in suggestions:
        poly_leg = next(leg for leg in s.legs if leg.venue == "polymarket")
        key = (s.kind, poly_leg.order_ref)
        rank = (s.sized_from_book, s.score())  # real depth beats a top-of-book guess
        if key not in best or rank > (best[key].sized_from_book, best[key].score()):
            best[key] = s
    return list(best.values())
