"""Fee models.

Kalshi (published fee schedule): taker fee per order =
    ceil_to_cent(rate * C * P * (1 - P))
with rate = 0.07 for most markets (some series differ; override in config).

Polymarket: taker fees are published per market in Gamma's `feeSchedule`
({"rate": 0.05, "exponent": 1, "takerOnly": true}); as of Sep 2026 sports
moneylines carry 0.03-0.05 and politics 0.04. Two curve shapes are plausible:
    "pq":   fee = C * rate * (P * (1 - P)) ** exponent        (default)
    "p_pq": fee = C * P * rate * (P * (1 - P)) ** exponent
"pq" is never smaller, so it's the conservative default until confirmed
against Polymarket's fee docs. Markets without a schedule fall back to
`taker_rate`. Fees are the difference between an arb and a loss at these
margins: verify before trading real money.
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
    taker_rate: float = 0.0  # fallback when a market publishes no feeSchedule
    exponent: float = 1.0
    formula: str = "pq"  # "pq" | "p_pq", see module docstring

    def cost(self, contracts: float, price: float, rate: float | None = None,
             exponent: float | None = None) -> float:
        r = self.taker_rate if rate is None else rate
        e = self.exponent if exponent is None else exponent
        if contracts <= 0 or r <= 0:
            return 0.0
        fee = contracts * r * (price * (1 - price)) ** e
        return fee * price if self.formula == "p_pq" else fee
