"""Opportunity math.

Two kinds of suggestion per matched proposition:

hedged_arb  Buy one side on Polymarket and the opposite side on Kalshi. Pays
            exactly $1 per contract pair at resolution (if both venues
            resolve the same way), so profit = 1 - both asks - both fees.

signal      Buy one side on Polymarket only, when it is cheap relative to a
            fair value estimated from both venues. Weights come from each
            venue's 24h volume (damped by `volume_weight_exponent`) divided by
            its bid/ask spread: the deeper, tighter book gets more say. If
            Polymarket out-trades Kalshi 20:1, Kalshi barely moves the fair
            value and few signals fire - which is the point.

Sizing walks the actual order books (when loaded) and stops where the
marginal contract no longer clears the edge threshold, the stake cap, or a
fraction of 24h volume.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime

from polyarb.config import Config
from polyarb.matching import Match, Pair
from polyarb.models import Level, Quote, Side

HARD_CAP_CONTRACTS = 100_000


@dataclass
class Leg:
    venue: str
    action: str  # e.g. "BUY Bills" (Polymarket token) or "BUY NO" (Kalshi)
    label: str
    order_ref: str
    contracts: int
    avg_price: float
    limit_price: float  # worst level touched: use as the limit price
    cost: float
    fee: float
    url: str


@dataclass
class Suggestion:
    kind: str  # "hedged_arb" | "signal"
    category: str
    league: str | None
    kalshi_event: str
    poly_event: str
    proposition: str
    legs: list[Leg]
    contracts: int
    total_cost: float  # incl. fees
    total_fees: float
    expected_profit: float  # guaranteed for arbs, expected for signals
    edge_per_contract: float
    roi: float
    apr: float | None
    days_to_close: float | None
    kalshi_mid: float | None
    poly_mid: float | None
    fair_prob: float | None
    price_ratio: float | None  # poly price / kalshi price, same side
    volume_ratio: float | None  # poly 24h $ / kalshi 24h $
    kalshi_weight: float | None
    confidence: float
    match_score: float
    sized_from_book: bool
    warnings: list[str] = field(default_factory=list)

    def score(self) -> float:
        return self.expected_profit * (1.0 if self.kind == "hedged_arb" else self.confidence)


# ------------------------------------------------------------ primitives

def fill(levels: list[Level], n: float) -> tuple[float, list[tuple[float, float]]] | None:
    """Cost and (price, qty) fills of buying n contracts, or None if too thin."""
    remaining, cost, fills = n, 0.0, []
    for lv in levels:
        if remaining <= 1e-9:
            break
        take = min(remaining, lv.size)
        if take > 0:
            cost += take * lv.price
            fills.append((lv.price, take))
            remaining -= take
    return None if remaining > 1e-9 else (cost, fills)


def _cum_sizes(levels: list[Level]) -> list[float]:
    out, total = [], 0.0
    for lv in levels:
        total += lv.size
        out.append(total)
    return out


def _depth(levels: list[Level]) -> float:
    return sum(lv.size for lv in levels)


class Pricer:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    def fee(self, quote: Quote, fills: list[tuple[float, float]]) -> float:
        if quote.venue == "kalshi":
            rate = self.cfg.kalshi_fees.rate_for(quote.market_id)
            return sum(self.cfg.kalshi_fees.cost(q, p, rate) for p, q in fills)
        return sum(self.cfg.poly_fees.cost(q, p, quote.fee_rate, quote.fee_exponent) for p, q in fills)

    def unit_fee(self, quote: Quote, price: float) -> float:
        """Approximate per-contract fee (no rounding), for screening/marginals."""
        if quote.venue == "kalshi":
            return self.cfg.kalshi_fees.rate_for(quote.market_id) * price * (1 - price)
        return self.cfg.poly_fees.cost(1, price, quote.fee_rate, quote.fee_exponent)


def fair_value(k: Quote, p: Quote, cfg: Config) -> tuple[float, float] | None:
    """(fair YES probability, Kalshi weight share)."""
    km, pm = k.mid(), p.mid()
    if km is None or pm is None:
        return None
    a = cfg.volume_weight_exponent
    ks = max(k.spread() if k.spread() is not None else 0.05, cfg.spread_floor)
    ps = max(p.spread() if p.spread() is not None else 0.05, cfg.spread_floor)
    wk = max(k.volume_24h_usd, 1.0) ** a / ks
    wp = max(p.volume_24h_usd, 1.0) ** a / ps
    share = wk / (wk + wp)
    return share * km + (1 - share) * pm, share


def _days(close: datetime | None, now: datetime) -> float | None:
    return None if close is None else max((close - now).total_seconds() / 86400, 0.0)


def _apr(roi: float, days: float | None) -> float | None:
    return None if days is None else roi * 365 / max(days, 1.0)


def _buy(side: Side, fallback: str) -> str:
    name = side.name or fallback
    return f"BUY {name.upper() if name.lower() in ('yes', 'no') else name}"


def _ratio(a: float | None, b: float | None) -> float | None:
    return a / b if a is not None and b else None


# ------------------------------------------------------------ evaluation

class Evaluator:
    def __init__(self, cfg: Config, now: datetime):
        self.cfg = cfg
        self.now = now
        self.pricer = Pricer(cfg)

    # Cheap top-of-book test: is it worth fetching order books?
    def screen(self, pair: Pair) -> bool:
        k, p, cfg = pair.kalshi, pair.poly, self.cfg
        if not self._liquid_enough(k, p) or self._diverged(k, p):
            return False
        for p_side, k_side in ((p.yes, k.no), (p.no, k.yes)):
            if p_side.ask and k_side.ask and self._in_band(p_side.ask):
                edge = 1 - p_side.ask - k_side.ask - self.pricer.unit_fee(p, p_side.ask) \
                    - self.pricer.unit_fee(k, k_side.ask)
                if edge >= cfg.min_arb_edge:
                    return True
        fv = fair_value(k, p, cfg)
        if fv and self._reference_ok(k):
            fair, _ = fv
            for side, prob in ((p.yes, fair), (p.no, 1 - fair)):
                if side.ask and self._in_band(side.ask):
                    if prob - side.ask - self.pricer.unit_fee(p, side.ask) >= cfg.min_signal_edge:
                        return True
        return False

    def evaluate(self, match: Match, pair: Pair) -> list[Suggestion]:
        k, p = pair.kalshi, pair.poly
        if not self._liquid_enough(k, p) or self._diverged(k, p):
            return []
        out = []
        if self.cfg.mode in ("arb", "both"):
            out += [self._hedge(match, pair, "YES"), self._hedge(match, pair, "NO")]
        if self.cfg.mode in ("signal", "both"):
            out += [self._signal(match, pair, "YES"), self._signal(match, pair, "NO")]
        return [s for s in out if s]

    # ---------------------------------------------------------- helpers

    def _liquid_enough(self, k: Quote, p: Quote) -> bool:
        return (k.volume_24h_usd >= self.cfg.min_kalshi_volume_24h_usd
                and p.volume_24h_usd >= self.cfg.min_poly_volume_24h_usd)

    def _diverged(self, k: Quote, p: Quote) -> bool:
        km, pm = k.mid(), p.mid()
        return km is not None and pm is not None and abs(km - pm) > self.cfg.max_mid_divergence

    def _in_band(self, price: float) -> bool:
        return self.cfg.min_price <= price <= self.cfg.max_price

    def _reference_ok(self, k: Quote) -> bool:
        s = k.spread()
        return s is not None and s <= self.cfg.max_kalshi_spread

    def _volume_cap(self, *quotes: Quote) -> float:
        # Treat a contract as <= $1 notional: conservative contract cap.
        return max(self.cfg.max_volume_fraction * min(q.volume_24h_usd for q in quotes), 0.0)

    def _context(self, match: Match, pair: Pair, side: str) -> dict:
        k, p = pair.kalshi, pair.poly
        fv = fair_value(k, p, self.cfg)
        k_side, p_side = (k.yes, p.yes) if side == "YES" else (k.no, p.no)
        label = p.label if pair.key != "__binary__" else match.poly.title
        return dict(
            category=match.kalshi.category,
            league=match.kalshi.league,
            kalshi_event=f"{match.kalshi.title} [{match.kalshi.event_id}]",
            poly_event=f"{match.poly.title} [{match.poly.event_id}]",
            proposition=f"{label} ({k.label} on Kalshi)",
            kalshi_mid=k.mid(),
            poly_mid=p.mid(),
            fair_prob=fv[0] if fv else None,
            kalshi_weight=fv[1] if fv else None,
            price_ratio=_ratio(p_side.ask, k_side.ask),
            volume_ratio=_ratio(p.volume_24h_usd, k.volume_24h_usd),
            match_score=pair.score,
        )

    def _warnings(self, match: Match, pair: Pair, sized_from_book: bool) -> list[str]:
        k, p = pair.kalshi, pair.poly
        w = list(match.notes)
        if not sized_from_book:
            w.append("sized from top-of-book only; depth unknown")
        if match.kalshi.category == "politics":
            w.append("verify both venues' resolution rules (call vs certification, withdrawals)")
        if match.kalshi.league == "nfl":
            w.append("NFL ties: check each venue's tie rule before hedging")
        # Kalshi game markets close ~2 weeks after kickoff by design; only flag politics.
        if (match.kalshi.category == "politics" and k.close_time and p.close_time
                and abs((k.close_time - p.close_time).days) > 3):
            w.append(f"close dates differ: kalshi {k.close_time.date()} vs polymarket {p.close_time.date()}")
        vr = _ratio(p.volume_24h_usd, k.volume_24h_usd)
        if vr and vr > 10:
            w.append(f"Kalshi volume is {vr:.0f}x thinner than Polymarket: weak reference")
        if pair.score < 0.95:
            w.append(f"fuzzy match (score {pair.score:.2f}): eyeball both markets")
        return w

    def _max_n(self, cost_at, upper: float) -> int:
        """Largest integer n <= upper whose total cost fits max_stake."""
        lo, hi = 0, int(min(upper, HARD_CAP_CONTRACTS))
        while lo < hi:
            mid = (lo + hi + 1) // 2
            c = cost_at(mid)
            if c is not None and c <= self.cfg.max_stake_usd:
                lo = mid
            else:
                hi = mid - 1
        return lo

    def _hedge(self, match: Match, pair: Pair, poly_side_name: str) -> Suggestion | None:
        k, p = pair.kalshi, pair.poly
        p_side: Side = p.yes if poly_side_name == "YES" else p.no
        k_side: Side = k.no if poly_side_name == "YES" else k.yes
        if p_side.ask is None or k_side.ask is None or not self._in_band(p_side.ask):
            return None
        pl, kl = p_side.ask_levels(), k_side.ask_levels()

        def at(n: int):
            pf, kf = fill(pl, n), fill(kl, n)
            if pf is None or kf is None:
                return None
            pfee, kfee = self.pricer.fee(p, pf[1]), self.pricer.fee(k, kf[1])
            return pf, kf, pfee, kfee, pf[0] + kf[0] + pfee + kfee

        def cost_at(n: int):
            r = at(n)
            return None if r is None else r[4]

        upper = min(_depth(pl), _depth(kl), self._volume_cap(k, p))
        n_max = self._max_n(cost_at, upper)
        if n_max < 1:
            return None
        points = {int(c) for c in _cum_sizes(pl) + _cum_sizes(kl) if 1 <= c <= n_max} | {n_max}
        best = None
        for n in sorted(points):
            r = at(n)
            if r is None:
                continue
            profit = n - r[4]
            if profit / n >= self.cfg.min_arb_edge and (best is None or profit > best[1]):
                best = (n, profit, r)
        if best is None:
            return None
        n, profit, (pf, kf, pfee, kfee, total) = best
        k_side_name = "NO" if poly_side_name == "YES" else "YES"
        legs = [
            Leg("polymarket", _buy(p_side, poly_side_name), p.label, p_side.order_ref, n, pf[0] / n,
                max(pr for pr, _ in pf[1]), pf[0], pfee, p.url),
            Leg("kalshi", f"BUY {k_side_name}", k.label, k_side.order_ref, n, kf[0] / n,
                max(pr for pr, _ in kf[1]), kf[0], kfee, k.url),
        ]
        sized = p_side.depth_loaded and k_side.depth_loaded
        days = _days(max((t for t in (k.close_time, p.close_time) if t), default=None), self.now)
        roi = profit / total
        if days is not None and _apr(roi, days) < self.cfg.min_arb_apr:
            return None
        ctx = self._context(match, pair, poly_side_name)
        return Suggestion(
            kind="hedged_arb", legs=legs, contracts=n, total_cost=total, total_fees=pfee + kfee,
            expected_profit=profit, edge_per_contract=profit / n, roi=roi, apr=_apr(roi, days),
            days_to_close=days, confidence=pair.score, sized_from_book=sized,
            warnings=self._warnings(match, pair, sized), **ctx,
        )

    def _signal(self, match: Match, pair: Pair, side_name: str) -> Suggestion | None:
        k, p, cfg = pair.kalshi, pair.poly, self.cfg
        fv = fair_value(k, p, cfg)
        if fv is None or not self._reference_ok(k):
            return None
        fair, share = fv
        prob = fair if side_name == "YES" else 1 - fair
        side = p.yes if side_name == "YES" else p.no
        top = side.ask
        if top is None or not self._in_band(top):
            return None
        if prob - top - self.pricer.unit_fee(p, top) < cfg.min_signal_edge:
            return None

        # Fractional Kelly on the top-of-book price, capped by max stake.
        kelly = max((prob - top) / (1 - top), 0.0)
        stake = min(cfg.max_stake_usd, cfg.kelly_fraction * cfg.bankroll_usd * kelly)
        cap = self._volume_cap(p)
        n, cost = 0.0, 0.0
        for lv in side.ask_levels():
            unit = lv.price + self.pricer.unit_fee(p, lv.price)
            if prob - unit < cfg.min_signal_edge:
                break
            take = min(lv.size, (stake - cost) / unit, cap - n)
            if take <= 0:
                break
            n += take
            cost += take * unit
        n_int = int(math.floor(n))
        if n_int < 1:
            return None
        f = fill(side.ask_levels(), n_int)
        if f is None:
            return None
        spent, fills = f
        fee = self.pricer.fee(p, fills)
        total = spent + fee
        ev = n_int * prob - total
        if ev <= 0:
            return None
        kspread = k.spread() or 0.0
        confidence = pair.score * min(1.0, 2 * share) * (1 - 0.5 * kspread / cfg.max_kalshi_spread)
        if confidence < cfg.min_signal_confidence:
            return None
        days = _days(p.close_time, self.now)
        roi = ev / total
        sized = side.depth_loaded
        leg = Leg("polymarket", _buy(side, side_name), p.label, side.order_ref, n_int, spent / n_int,
                  max(pr for pr, _ in fills), spent, fee, p.url)
        ctx = self._context(match, pair, side_name)
        warnings = self._warnings(match, pair, sized)
        warnings.append("directional: unhedged, loses if the outcome goes against you")
        return Suggestion(
            kind="signal", legs=[leg], contracts=n_int, total_cost=total, total_fees=fee,
            expected_profit=ev, edge_per_contract=ev / n_int, roi=roi, apr=_apr(roi, days),
            days_to_close=days, confidence=confidence, sized_from_book=sized,
            warnings=warnings, **ctx,
        )
