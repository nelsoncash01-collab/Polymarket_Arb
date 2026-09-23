"""Polymarket client: Gamma API for market discovery, CLOB API for order books.

Gamma encodes `outcomes`, `outcomePrices` and `clobTokenIds` as JSON strings.
`bestBid` / `bestAsk` refer to the first outcome's token.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import replace
from datetime import datetime, timezone

from polyarb.config import Config
from polyarb.http import ClientError, Fetcher
from polyarb.kalshi import parse_time
from polyarb.matching import outcome_key
from polyarb.models import Event, Level, Quote, Side
from polyarb.teams import fallback_key, has_table, resolve_team

log = logging.getLogger(__name__)

_SLUG_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})$")
_NOT_TEAMS = {"yes", "no", "over", "under", "draw", "tie"}
_NOT_MONEYLINE = re.compile(r"spread|o/u|over/under|total|1st|first half|1h|quarter|period|inning", re.I)


def _jl(value) -> list:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return []
    return list(value or [])


def _f(value) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if 0 < v < 1 else None


def _num(m: dict, *names: str) -> float:
    for n in names:
        try:
            if m.get(n) is not None:
                return float(m[n])
        except (TypeError, ValueError):
            pass
    return 0.0


def _tradeable(m: dict) -> bool:
    return (
        m.get("active", True)
        and not m.get("closed", False)
        and m.get("acceptingOrders", True) is not False
        and m.get("enableOrderBook", True) is not False
        and len(_jl(m.get("clobTokenIds"))) == 2
    )


def _fee_schedule(m: dict) -> tuple[float | None, float | None]:
    """(rate, exponent) from Gamma's per-market feeSchedule; None = use config."""
    if m.get("feesEnabled") is False:
        return 0.0, None
    sched = m.get("feeSchedule")
    if isinstance(sched, dict) and sched.get("rate") is not None:
        return float(sched["rate"]), (float(sched["exponent"]) if sched.get("exponent") is not None else None)
    return None, None


def binary_quotes(m: dict) -> tuple[Side, Side]:
    """Sides for outcome[0] (YES) and outcome[1] (NO) of a two-token market."""
    t0, t1 = _jl(m.get("clobTokenIds"))
    n0, n1 = (str(o) for o in _jl(m.get("outcomes")))
    bid0, ask0 = _f(m.get("bestBid")), _f(m.get("bestAsk"))
    first = Side(ask=ask0, bid=bid0, order_ref=t0, name=n0)
    second = Side(
        ask=round(1 - bid0, 4) if bid0 else None,
        bid=round(1 - ask0, 4) if ask0 else None,
        order_ref=t1,
        name=n1,
    )
    return first, second


def _quote(m: dict, label: str, yes: Side, no: Side, url: str) -> Quote:
    rate, exponent = _fee_schedule(m)
    return Quote(
        venue="polymarket",
        market_id=m.get("conditionId") or str(m.get("id", "")),
        label=label,
        yes=yes,
        no=no,
        volume_usd=_num(m, "volumeNum", "volume"),
        volume_24h_usd=_num(m, "volume24hr", "volume24hrClob"),
        liquidity_usd=_num(m, "liquidityNum", "liquidity"),
        close_time=parse_time(m.get("endDate")),
        url=url,
        fee_rate=rate,
        fee_exponent=exponent,
    )


def event_date(ev: dict, m: dict | None = None) -> datetime | None:
    # eventDate is the local game date (matches Kalshi tickers); the slug's
    # date can be the UTC date, a day later for night games.
    s = _SLUG_DATE.search(ev.get("eventDate") or "") or _SLUG_DATE.search(ev.get("slug") or "")
    if s:
        return datetime(int(s.group(1)), int(s.group(2)), int(s.group(3)), tzinfo=timezone.utc)
    for value in ((m or {}).get("gameStartTime"), ev.get("startTime"), ev.get("endDate")):
        dt = parse_time(value.replace(" ", "T") if isinstance(value, str) else None)
        if dt:
            return dt
    return None


def parse_sports_event(ev: dict, league: str) -> Event | None:
    url = f"https://polymarket.com/event/{ev.get('slug', '')}"
    candidates = []
    for m in ev.get("markets") or []:
        outcomes = _jl(m.get("outcomes"))
        if not _tradeable(m) or len(outcomes) != 2:
            continue
        if any(str(o).strip().lower() in _NOT_TEAMS for o in outcomes):
            continue
        smt = (m.get("sportsMarketType") or "").lower()
        if smt and smt != "moneyline":
            continue
        if not smt and _NOT_MONEYLINE.search(m.get("question") or ""):
            continue
        candidates.append(m)
    if not candidates:
        return None
    m = max(candidates, key=lambda x: _num(x, "volumeNum", "volume"))
    outcomes = [str(o) for o in _jl(m.get("outcomes"))]
    keys = []
    for name in outcomes:
        key = resolve_team(league, name)
        if key is None:
            if has_table(league):
                log.debug("polymarket: unresolved team %r in %s", name, ev.get("slug"))
                return None
            key = fallback_key(league, name)
        keys.append(key)
    if keys[0] == keys[1]:
        return None
    s0, s1 = binary_quotes(m)
    quotes = {
        # "Team A wins": YES = A's token, NO = B's token (and vice versa).
        keys[0]: _quote(m, outcomes[0], s0, s1, url),
        keys[1]: _quote(m, outcomes[1], replace(s1), replace(s0), url),
    }
    return Event(
        venue="polymarket",
        event_id=ev.get("slug") or str(ev.get("id", "")),
        title=ev.get("title") or m.get("question", ""),
        category="sports",
        league=league,
        date=event_date(ev, m),
        teams=tuple(sorted(quotes)),
        quotes=quotes,
    )


def parse_politics_event(ev: dict) -> Event | None:
    url = f"https://polymarket.com/event/{ev.get('slug', '')}"
    markets = [m for m in ev.get("markets") or [] if _tradeable(m)]
    markets = [m for m in markets if [str(o).lower() for o in _jl(m.get("outcomes"))] == ["yes", "no"]]
    if not markets:
        return None
    quotes: dict[str, Quote] = {}
    for m in markets:
        yes, no = binary_quotes(m)
        label = m.get("groupItemTitle") or m.get("question") or ""
        key = "__binary__" if len(markets) == 1 else outcome_key(label)
        quotes.setdefault(key, _quote(m, label, yes, no, url))
    closes = [q.close_time for q in quotes.values() if q.close_time]
    return Event(
        venue="polymarket",
        event_id=ev.get("slug") or str(ev.get("id", "")),
        title=ev.get("title", ""),
        category="politics",
        date=max(closes) if closes else None,
        quotes=quotes,
    )


class PolymarketClient:
    def __init__(self, cfg: Config, fetcher: Fetcher):
        self.cfg = cfg
        self.fetch = fetcher

    def _events(self, tag: str) -> list[dict]:
        # Most-traded first: Gamma rejects offsets past ~2000 (HTTP 422), so if
        # a tag is truncated, what gets dropped is the illiquid tail.
        out, limit = [], 100
        for page in range(self.cfg.max_pages):
            params = {"tag_slug": tag, "active": "true", "closed": "false", "limit": limit,
                      "offset": page * limit, "order": "volume24hr", "ascending": "false"}
            try:
                data = self.fetch.get_json(f"{self.cfg.gamma_base}/events", params)
            except ClientError as exc:
                if page == 0:
                    raise
                log.warning("polymarket %s: stopped paging at offset %d (%s)", tag, page * limit, exc)
                break
            rows = data if isinstance(data, list) else data.get("events") or data.get("data") or []
            out.extend(rows)
            if len(rows) < limit:
                break
        return out

    def sports_events(self, league: str) -> list[Event]:
        tag = self.cfg.poly_sports_tags.get(league)
        if not tag:
            return []
        events = [e for e in (parse_sports_event(ev, league) for ev in self._events(tag)) if e]
        log.info("polymarket %s: %d game events", league, len(events))
        return events

    def politics_events(self) -> list[Event]:
        seen, events = set(), []
        for tag in self.cfg.poly_politics_tags:
            for ev in self._events(tag):
                key = ev.get("id") or ev.get("slug")
                if key in seen:
                    continue
                seen.add(key)
                parsed = parse_politics_event(ev)
                if parsed:
                    events.append(parsed)
        log.info("polymarket politics: %d events", len(events))
        return events

    def load_book(self, quote: Quote) -> None:
        for side in (quote.yes, quote.no):
            data = self.fetch.get_json(f"{self.cfg.clob_base}/book", {"token_id": side.order_ref})
            side.asks = sorted(
                (Level(float(a["price"]), float(a["size"])) for a in data.get("asks") or []),
                key=lambda lv: lv.price,
            )
            bids = [float(b["price"]) for b in data.get("bids") or []]
            side.depth_loaded = True
            side.ask = side.asks[0].price if side.asks else None
            side.bid = max(bids) if bids else None
