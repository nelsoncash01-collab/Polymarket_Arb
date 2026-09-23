"""Fee models.

Kalshi (published fee schedule): taker fee per order =
    ceil_to_cent(rate * C * P * (1 - P))
with rate = 0.07 for most markets (some series differ; override in config).

Polymarket: most markets historically charged no trading fee, but fee-enabled
markets use a curve of the form
    fee = C * P * rate * (P * (1 - P)) ** exponent
Rates differ per market type and change over time, so they are config-driven.
VERIFY CURRENT RATES before trading real money; fees are the difference
between an arb and a loss at these margins.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


def ceil_cents(x: float) -> float:
    # Tolerance keeps float noise (e.g. 1.0000000001 cents) from adding a cent.
    return math.ceil(round(x * 100, 6)) / 100


@dataclass
class KalshiFees:
    taker_rate: float = 0.07

    series_rates: dict[str, float] = field(default_factory=dict)  # e.g. {"KXINX": 0.035}

    def rate_for(self, ticker: str) -> float:
        return self.series_rates.get(ticker.split("-")[0], self.taker_rate)

    def cost(self, contracts: float, price: float, rate: float | None = None) -> float:
        if contracts <= 0:
            return 0.0
        r = self.taker_rate if rate is None else rate
        return ceil_cents(r * contracts * price * (1 - price))


@dataclass
class PolymarketFees:
    taker_rate: float = 0.0
    exponent: float = 1.0

    def cost(self, contracts: float, price: float, rate: float | None = None) -> float:
        r = self.taker_rate if rate is None else rate
        if contracts <= 0 or r <= 0:
            return 0.0
        return contracts * price * r * (price * (1 - price)) ** self.exponent
