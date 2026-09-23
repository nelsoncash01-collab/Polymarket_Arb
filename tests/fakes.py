"""API-shaped payloads mirroring Kalshi trade-api v2 and Polymarket Gamma/CLOB."""

import json

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def kalshi_market(ticker, sub_title, yes_bid, yes_ask, volume_24h=50_000, dollars=False, **extra):
    m = {
        "ticker": ticker,
        "status": "active",
        "yes_sub_title": sub_title,
        "volume": volume_24h * 3,
        "volume_24h": volume_24h,
        "close_time": "2026-10-08T00:00:00Z",
        **extra,
    }
    prices = {"yes_bid": yes_bid, "yes_ask": yes_ask, "no_bid": 1 - yes_ask, "no_ask": 1 - yes_bid,
              "last_price": (yes_bid + yes_ask) / 2}
    for k, v in prices.items():
        if dollars:
            m[f"{k}_dollars"] = f"{v:.4f}"
        else:
            m[k] = round(v * 100)
    return m


def poly_market(outcomes, best_bid, best_ask, tokens, volume_24h=40_000, **extra):
    return {
        "id": tokens[0][:6],
        "conditionId": "0x" + tokens[0],
        "question": " vs. ".join(outcomes),
        "outcomes": json.dumps(outcomes),
        "outcomePrices": json.dumps([str(best_ask), str(round(1 - best_ask, 3))]),
        "clobTokenIds": json.dumps(tokens),
        "bestBid": best_bid,
        "bestAsk": best_ask,
        "volumeNum": volume_24h * 5,
        "volume24hr": volume_24h,
        "liquidityNum": 20_000,
        "endDate": "2026-09-25T04:00:00Z",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        **extra,
    }


class FakeFetcher:
    """Routes (url, selected params) to canned payloads; records calls."""

    def __init__(self):
        self.routes = {}
        self.calls = []

    def add(self, url, payload, **params):
        self.routes[(url, tuple(sorted(params.items())))] = payload

    def get_json(self, url, params=None):
        self.calls.append((url, params))
        params = params or {}
        for (u, sel), payload in self.routes.items():
            if u == url and all(str(params.get(k)) == str(v) for k, v in sel):
                return payload
        if url.endswith("/events") and "gamma" in url:
            return []
        if url.endswith("/events"):
            return {"events": [], "cursor": ""}
        raise KeyError(f"no route for {url} {params}")


def nfl_world(fetcher: FakeFetcher, dollars=False):
    """KC @ BUF: Kalshi KC 38/40, Polymarket Chiefs 45/46 -> buy KC on Kalshi, Bills on Poly."""
    fetcher.add(f"{KALSHI}/events", {"cursor": "", "events": [{
        "event_ticker": "KXNFLGAME-26SEP24KCBUF",
        "series_ticker": "KXNFLGAME",
        "title": "Kansas City at Buffalo",
        "category": "Sports",
        "markets": [
            kalshi_market("KXNFLGAME-26SEP24KCBUF-KC", "Kansas City", 0.38, 0.40, dollars=dollars),
            kalshi_market("KXNFLGAME-26SEP24KCBUF-BUF", "Buffalo", 0.60, 0.62, dollars=dollars),
        ],
    }]}, series_ticker="KXNFLGAME")
    fetcher.add(f"{GAMMA}/events", [{
        "id": "9001",
        "slug": "nfl-kc-buf-2026-09-24",
        "title": "Chiefs vs. Bills",
        "markets": [
            poly_market(["Chiefs", "Bills"], 0.45, 0.46, ["tokKC", "tokBUF"], sportsMarketType="moneyline",
                        gameStartTime="2026-09-25 00:15:00+00"),
            poly_market(["Over", "Under"], 0.50, 0.52, ["tokO", "tokU"], sportsMarketType="totals"),
        ],
    }], tag_slug="nfl")
    # Kalshi books are bids only. NO bids at 0.60 x100, 0.58 x200 => YES asks 0.40 x100, 0.42 x200.
    fetcher.add(f"{KALSHI}/markets/KXNFLGAME-26SEP24KCBUF-KC/orderbook",
                {"orderbook": {"yes": [[36, 500], [38, 100]], "no": [[58, 200], [60, 100]]}})
    fetcher.add(f"{CLOB}/book", {"bids": [{"price": "0.44", "size": "300"}],
                                 "asks": [{"price": "0.57", "size": "500"}, {"price": "0.55", "size": "150"}]},
                token_id="tokBUF")
    fetcher.add(f"{CLOB}/book", {"bids": [{"price": "0.45", "size": "150"}],
                                 "asks": [{"price": "0.46", "size": "400"}]}, token_id="tokKC")


def ohio_world(fetcher: FakeFetcher):
    fetcher.add(f"{KALSHI}/events", {"cursor": "", "events": [
        {
            "event_ticker": "SENATEOH-26",
            "series_ticker": "SENATEOH",
            "title": "Ohio Senate race winner 2026?",
            "category": "Politics",
            "markets": [
                kalshi_market("SENATEOH-26-D", "Democratic party", 0.43, 0.47, volume_24h=400_000),
                kalshi_market("SENATEOH-26-R", "Republican party", 0.53, 0.57, volume_24h=400_000),
            ],
        },
        {   # decoy: must not match Ohio
            "event_ticker": "SENATEWV-26",
            "title": "West Virginia Senate race winner 2026?",
            "category": "Politics",
            "markets": [kalshi_market("SENATEWV-26-R", "Republican party", 0.90, 0.92)],
        },
    ]})
    fetcher.add(f"{GAMMA}/events", [{
        "id": "7001",
        "slug": "ohio-senate-election-winner",
        "title": "Ohio Senate Election Winner",
        "markets": [
            poly_market(["Yes", "No"], 0.36, 0.38, ["tokD", "tokDn"], volume_24h=10_000,
                        groupItemTitle="Democrat", endDate="2026-11-04T00:00:00Z"),
            poly_market(["Yes", "No"], 0.58, 0.60, ["tokR", "tokRn"], volume_24h=10_000,
                        groupItemTitle="Republican", endDate="2026-11-04T00:00:00Z"),
        ],
    }], tag_slug="elections")
