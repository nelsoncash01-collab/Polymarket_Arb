"""Kalshi public market-data client (no auth needed for reads).

Kalshi has been migrating from integer-cent fields (`yes_ask: 45`) to
fixed-point dollar strings (`yes_ask_dollars: "0.4500"`); both are accepted.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.http import Fetcher
from polyarb.matching import outcome_key
from polyarb.models import Event, Level, Quote, Side
from polyarb.teams import fallback_key, has_table, resolve_team

log = logging.getLogger(__name__)

_MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_TICKER_DATE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})")


def parse_ticker_date(ticker: str) -> datetime | None:
    """KXNFLGAME-26SEP24KCBUF -> 2026-09-24 (the game's local date)."""
    m = _TICKER_DATE.search(ticker or "")
    if not m or m.group(2) not in _MONTHS:
        return None
    try:
        return datetime(2000 + int(m.group(1)), _MONTHS[m.group(2)], int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _price(m: dict, name: str) -> float | None:
    raw = m.get(f"{name}_dollars")
    if raw not in (None, ""):
        value = float(raw)
    elif m.get(name) is not None:
        value = float(m[name]) / 100
    else:
        return None
    return value if 0 < value < 1 else None  # 0 / 100c means no resting orders


def _number(m: dict, name: str) -> float:
    for key in (f"{name}_fp", name):
        if m.get(key) not in (None, ""):
            return float(m[key])
    return 0.0


def market_to_quote(m: dict, series: str = "") -> Quote:
    ticker = m["ticker"]
    yes_ask, yes_bid = _price(m, "yes_ask"), _price(m, "yes_bid")
    no_ask, no_bid = _price(m, "no_ask"), _price(m, "no_bid")
    # A YES bid is a NO offer at the complement, and vice versa.
    no_ask = no_ask if no_ask is not None else (1 - yes_bid if yes_bid else None)
    yes_ask = yes_ask if yes_ask is not None else (1 - no_bid if no_bid else None)
    ref_price = _price(m, "last_price") or yes_ask or 0.5
    liquidity = float(m["liquidity_dollars"]) if m.get("liquidity_dollars") else _number(m, "liquidity") / 100
    return Quote(
        venue="kalshi",
        market_id=ticker,
        label=m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or ticker,
        yes=Side(ask=yes_ask, bid=yes_bid, order_ref=f"{ticker} yes", name="Yes"),
        no=Side(ask=no_ask, bid=no_bid, order_ref=f"{ticker} no", name="No"),
        # Kalshi reports volume in contracts; approximate $ notional of the YES side.
        volume_usd=_number(m, "volume") * ref_price,
        volume_24h_usd=_number(m, "volume_24h") * ref_price,
        liquidity_usd=liquidity,
        close_time=parse_time(m.get("close_time")),
        url=f"https://kalshi.com/markets/{(series or ticker.split('-')[0]).lower()}",
    )


def _is_open(m: dict) -> bool:
    return m.get("status") in (None, "active", "open", "initialized")


class KalshiClient:
    def __init__(self, cfg: Config, fetcher: Fetcher):
        self.cfg = cfg
        self.fetch = fetcher

    def _events(self, **params) -> list[dict]:
        out, cursor = [], None
        for _ in range(self.cfg.max_pages):
            q = {"status": "open", "with_nested_markets": "true", "limit": 200, **params}
            if cursor:
                q["cursor"] = cursor
            data = self.fetch.get_json(f"{self.cfg.kalshi_base}/events", q)
            out.extend(data.get("events") or [])
            cursor = data.get("cursor")
            if not cursor:
                break
        return out

    def sports_events(self, league: str) -> list[Event]:
        series = self.cfg.kalshi_sports_series.get(league)
        if not series:
            return []
        events = []
        for ev in self._events(series_ticker=series):
            parsed = parse_sports_event(ev, league, series)
            if parsed:
                events.append(parsed)
        log.info("kalshi %s: %d game events", league, len(events))
        return events

    def politics_events(self) -> list[Event]:
        wanted = {c.lower() for c in self.cfg.kalshi_politics_categories}
        events = []
        for ev in self._events():
            if (ev.get("category") or "").lower() not in wanted:
                continue
            parsed = parse_politics_event(ev)
            if parsed:
                events.append(parsed)
        log.info("kalshi politics: %d events", len(events))
        return events

    def load_book(self, quote: Quote, depth: int = 50) -> None:
        data = self.fetch.get_json(f"{self.cfg.kalshi_base}/markets/{quote.market_id}/orderbook", {"depth": depth})
        yes_bids, no_bids = parse_orderbook(data)
        # Kalshi books hold bids only: buying YES means hitting NO bids at 1 - p.
        quote.yes.asks = sorted((Level(round(1 - p, 4), q) for p, q in no_bids), key=lambda lv: lv.price)
        quote.no.asks = sorted((Level(round(1 - p, 4), q) for p, q in yes_bids), key=lambda lv: lv.price)
        quote.yes.depth_loaded = quote.no.depth_loaded = True
        if quote.yes.asks:
            quote.yes.ask = quote.yes.asks[0].price
        if quote.no.asks:
            quote.no.ask = quote.no.asks[0].price


def parse_orderbook(data: dict) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    fp = data.get("orderbook_fp") or {}
    if fp.get("yes_dollars") is not None or fp.get("no_dollars") is not None:
        conv = lambda rows: [(float(p), float(q)) for p, q in rows or []]  # noqa: E731
        return conv(fp.get("yes_dollars")), conv(fp.get("no_dollars"))
    ob = data.get("orderbook") or {}
    if ob.get("yes_dollars") is not None or ob.get("no_dollars") is not None:
        conv = lambda rows: [(float(p), float(q)) for p, q in rows or []]  # noqa: E731
        return conv(ob.get("yes_dollars")), conv(ob.get("no_dollars"))
    conv = lambda rows: [(float(p) / 100, float(q)) for p, q in rows or []]  # noqa: E731
    return conv(ob.get("yes")), conv(ob.get("no"))


def parse_sports_event(ev: dict, league: str, series: str = "") -> Event | None:
    markets = [m for m in ev.get("markets") or [] if _is_open(m)]
    if len(markets) != 2:  # 3-way (tie/draw) markets aren't comparable to 2-way ones
        return None
    quotes: dict[str, Quote] = {}
    for m in markets:
        label = m.get("yes_sub_title") or m.get("subtitle") or ""
        suffix = m["ticker"].rsplit("-", 1)[-1]
        key = resolve_team(league, label) or resolve_team(league, suffix)
        if key is None:
            if has_table(league):
                log.debug("kalshi: unresolved team %r in %s", label, m["ticker"])
                return None
            key = fallback_key(league, label)
        quotes[key] = market_to_quote(m, series)
    if len(quotes) != 2:
        return None
    return Event(
        venue="kalshi",
        event_id=ev.get("event_ticker", ""),
        title=ev.get("title", ""),
        category="sports",
        league=league,
        date=parse_ticker_date(ev.get("event_ticker", "")),
        teams=tuple(sorted(quotes)),
        quotes=quotes,
    )


def parse_politics_event(ev: dict) -> Event | None:
    markets = [m for m in ev.get("markets") or [] if _is_open(m)]
    if not markets:
        return None
    quotes: dict[str, Quote] = {}
    if len(markets) == 1:
        quotes["__binary__"] = market_to_quote(markets[0], ev.get("series_ticker", ""))
    else:
        for m in markets:
            q = market_to_quote(m, ev.get("series_ticker", ""))
            quotes.setdefault(outcome_key(q.label), q)
    closes = [q.close_time for q in quotes.values() if q.close_time]
    title = ev.get("title", "")
    if ev.get("sub_title"):
        title = f"{title} {ev['sub_title']}"
    return Event(
        venue="kalshi",
        event_id=ev.get("event_ticker", ""),
        title=title,
        category="politics",
        date=max(closes) if closes else None,
        quotes=quotes,
    )
