"""Venue-neutral data model.

Every tradable thing is reduced to a binary *proposition* ("Chiefs beat the
Bills", "Sherrod Brown wins OH-Sen") with a YES side and a NO side, each of
which can be bought on a venue at some ask price. Prices are in dollars per
contract that pays $1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class Level:
    price: float  # dollars per contract, 0 < price < 1
    size: float  # contracts available at this price


@dataclass
class Side:
    """One buyable side (YES or NO) of a proposition on one venue."""

    ask: float | None  # best price to buy
    bid: float | None  # best price to sell
    order_ref: str  # what you'd submit an order against (ticker/side or token id)
    asks: list[Level] = field(default_factory=list)  # depth, ascending price
    depth_loaded: bool = False  # True once `asks` came from a real order book
    name: str = ""  # what the venue calls this side, e.g. "Bills" or "Yes"

    def ask_levels(self) -> list[Level]:
        if self.asks:
            return self.asks
        if self.ask is None:
            return []
        # Top-of-book only, unknown size: treated as unbounded, capped later by
        # stake / volume limits. Real sizing requires the book (see scanner).
        return [Level(self.ask, float("inf"))]


@dataclass
class Quote:
    """A binary proposition as listed on one venue."""

    venue: str  # "kalshi" | "polymarket"
    market_id: str
    label: str  # outcome label as the venue shows it
    yes: Side
    no: Side
    volume_usd: float = 0.0
    volume_24h_usd: float = 0.0
    liquidity_usd: float = 0.0
    close_time: datetime | None = None
    url: str = ""
    fee_rate: float | None = None  # market-specific taker rate, if the venue publishes one
    fee_exponent: float | None = None

    def mid(self) -> float | None:
        bid, ask = self.yes.bid, self.yes.ask
        if bid is not None and ask is not None:
            return (bid + ask) / 2
        return ask if ask is not None else bid

    def spread(self) -> float | None:
        if self.yes.bid is None or self.yes.ask is None:
            return None
        return max(self.yes.ask - self.yes.bid, 0.0)


@dataclass
class Event:
    """A group of propositions on one venue (a game, an election)."""

    venue: str
    event_id: str
    title: str
    category: str  # "sports" | "politics"
    league: str | None = None  # sports only, e.g. "nfl"
    date: datetime | None = None  # game date / close time used for matching
    teams: tuple[str, ...] = ()  # sports: canonical team keys
    # outcome key -> Quote. Sports keys are canonical team ids ("nfl:KC");
    # politics keys come from matching.outcome_key().
    quotes: dict[str, Quote] = field(default_factory=dict)
