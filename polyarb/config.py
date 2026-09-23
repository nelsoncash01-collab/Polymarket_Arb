"""Scanner configuration. Every threshold lives here; override via JSON file."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path

from polyarb.fees import KalshiFees, PolymarketFees

# league -> Kalshi series ticker for single-game winner markets
DEFAULT_KALSHI_SPORTS_SERIES = {
    "nfl": "KXNFLGAME",
    "nba": "KXNBAGAME",
    "mlb": "KXMLBGAME",
    "nhl": "KXNHLGAME",
    "wnba": "KXWNBAGAME",
    "ncaaf": "KXNCAAFGAME",
    "ncaamb": "KXNCAAMBGAME",
}

# league -> Polymarket Gamma tag slug
DEFAULT_POLY_SPORTS_TAGS = {
    "nfl": "nfl",
    "nba": "nba",
    "mlb": "mlb",
    "nhl": "nhl",
    "wnba": "wnba",
    "ncaaf": "cfb",
    "ncaamb": "cbb",
}


@dataclass
class Config:
    kalshi_base: str = "https://api.elections.kalshi.com/trade-api/v2"
    gamma_base: str = "https://gamma-api.polymarket.com"
    clob_base: str = "https://clob.polymarket.com"

    leagues: list[str] = field(default_factory=lambda: ["nfl", "nba", "mlb", "nhl", "wnba", "ncaaf"])
    kalshi_sports_series: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_KALSHI_SPORTS_SERIES))
    poly_sports_tags: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_POLY_SPORTS_TAGS))
    kalshi_politics_categories: list[str] = field(default_factory=lambda: ["Politics", "Elections"])
    poly_politics_tags: list[str] = field(default_factory=lambda: ["elections", "politics"])
    max_pages: int = 25  # pagination safety cap per listing

    kalshi_fees: KalshiFees = field(default_factory=KalshiFees)
    poly_fees: PolymarketFees = field(default_factory=PolymarketFees)

    # "arb": hedged cross-venue only; "signal": Polymarket-only; "both"
    mode: str = "both"

    # --- filters ---
    min_price: float = 0.03  # ignore tails: resolution/fee risk dominates there
    max_price: float = 0.97
    min_kalshi_volume_24h_usd: float = 500.0
    min_poly_volume_24h_usd: float = 500.0
    max_kalshi_spread: float = 0.08  # a wide Kalshi book is not a usable reference
    min_match_score: float = 0.85
    # Mids further apart than this are almost always two different questions
    # (e.g. "mayor by Oct 1" vs "next mayor"), not free money. Skip them.
    max_mid_divergence: float = 0.20
    # Manual overrides for election matching: [[kalshi_event_ticker, poly_event_slug], ...]
    force_pairs: list[list[str]] = field(default_factory=list)
    block_pairs: list[list[str]] = field(default_factory=list)

    # --- hedged arb (buy both sides across venues) ---
    min_arb_edge: float = 0.01  # net $ per contract after fees
    # 3% locked for two years is worse than T-bills. Arbs must beat this
    # annualized; set 0 to disable.
    min_arb_apr: float = 0.08

    # --- directional signal (trade Polymarket only, Kalshi as reference) ---
    min_signal_edge: float = 0.03  # net expected $ per contract after fees
    min_signal_confidence: float = 0.35
    volume_weight_exponent: float = 0.5  # fair value weight ~ vol^a / spread
    spread_floor: float = 0.01

    # --- sizing ---
    bankroll_usd: float = 1000.0
    max_stake_usd: float = 250.0  # per opportunity
    kelly_fraction: float = 0.25  # signal trades only; arbs are sized by depth
    max_volume_fraction: float = 0.10  # never take more than this share of 24h volume
    fetch_books: bool = True  # walk real order books before sizing

    @classmethod
    def load(cls, path: str | Path | None) -> "Config":
        cfg = cls()
        if path:
            _merge(cfg, json.loads(Path(path).read_text()))
        return cfg

    def to_dict(self) -> dict:
        return asdict(self)


def _merge(obj, data: dict) -> None:
    known = {f.name for f in fields(obj)}
    for key, value in data.items():
        if key not in known:
            raise ValueError(f"unknown config key: {key}")
        current = getattr(obj, key)
        if is_dataclass(current) and isinstance(value, dict):
            _merge(current, value)
        else:
            setattr(obj, key, value)
