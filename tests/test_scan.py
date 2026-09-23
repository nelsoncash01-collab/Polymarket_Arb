from datetime import datetime, timezone

import pytest

from polyarb.config import Config
from polyarb.scanner import scan
from tests.fakes import FakeFetcher, nfl_world, ohio_world

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


@pytest.mark.parametrize("dollars", [False, True])
def test_nfl_end_to_end_arb(dollars):
    f = FakeFetcher()
    nfl_world(f, dollars=dollars)
    cfg = Config(leagues=["nfl"], mode="arb")
    res = scan(cfg, f, politics=False, now=NOW)
    assert len(res.matches) == 1
    m = res.matches[0]
    assert m.kalshi.event_id == "KXNFLGAME-26SEP24KCBUF" and m.poly.event_id == "nfl-kc-buf-2026-09-24"
    arbs = [s for s in res.suggestions if s.kind == "hedged_arb"]
    assert len(arbs) == 1
    s = arbs[0]
    # Buy Bills token on Polymarket + KC YES on Kalshi
    poly_leg, kalshi_leg = s.legs
    assert poly_leg.order_ref == "tokBUF" and kalshi_leg.order_ref == "KXNFLGAME-26SEP24KCBUF-KC yes"
    assert poly_leg.action == "BUY Bills" and kalshi_leg.action == "BUY YES"
    assert s.contracts == 150 and s.sized_from_book
    assert s.expected_profit == pytest.approx(150 - 61 - 82.5 - 2.54)
    assert any("NFL ties" in w for w in s.warnings)


def test_ohio_election_matches_and_ignores_decoy():
    f = FakeFetcher()
    ohio_world(f)
    cfg = Config(fetch_books=False, poly_politics_tags=["elections"])
    res = scan(cfg, f, sports=False, now=NOW)
    assert len(res.matches) == 1
    m = res.matches[0]
    assert m.kalshi.event_id == "SENATEOH-26"
    assert {p.key for p in m.pairs} == {"party:DEM", "party:GOP"}
    dem = [s for s in res.suggestions if s.proposition.startswith("Democrat")]
    assert dem, "Polymarket Dem at 0.38 vs Kalshi 0.45 mid with 40x Kalshi volume should flag"
    assert all(not s.sized_from_book for s in dem)
    signal = next(s for s in dem if s.kind == "signal")
    assert signal.legs[0].action == "BUY YES" and signal.legs[0].venue == "polymarket"
    assert any("resolution rules" in w for w in signal.warnings)


def test_doubleheader_is_skipped():
    f = FakeFetcher()
    nfl_world(f)
    # duplicate the Polymarket game under another slug the same day
    (key, payload), = [(k, v) for k, v in f.routes.items() if k[0].endswith("/events") and "gamma" in k[0]]
    dup = dict(payload[0], slug="nfl-kc-buf-2026-09-24-game-2", id="9002")
    f.routes[key] = payload + [dup]
    res = scan(Config(leagues=["nfl"]), f, politics=False, now=NOW)
    assert res.matches == []


def test_book_fetch_failure_falls_back_to_top_of_book():
    f = FakeFetcher()
    nfl_world(f)
    f.routes = {k: v for k, v in f.routes.items() if "/book" not in k[0] and "orderbook" not in k[0]}
    res = scan(Config(leagues=["nfl"], mode="arb"), f, politics=False, now=NOW)
    [s] = res.suggestions
    assert not s.sized_from_book
    assert any("top-of-book" in w for w in s.warnings)
