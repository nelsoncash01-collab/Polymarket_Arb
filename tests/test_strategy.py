from datetime import datetime, timezone

import pytest

from polyarb.config import Config
from polyarb.matching import Match, Pair
from polyarb.models import Event, Level, Quote, Side
from polyarb.strategy import Evaluator, fair_value, fill

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def quote(venue, bid, ask, vol, yes_asks=None, no_asks=None):
    yes = Side(ask, bid, f"{venue}-yes", asks=yes_asks or [], depth_loaded=bool(yes_asks))
    no = Side(round(1 - bid, 4), round(1 - ask, 4), f"{venue}-no", asks=no_asks or [], depth_loaded=bool(no_asks))
    return Quote(venue, f"{venue}-MKT", "Team", yes, no, volume_24h_usd=vol,
                 close_time=datetime(2026, 9, 25, tzinfo=timezone.utc))


def match_for(k, p, category="sports"):
    ke = Event("kalshi", "K", "k title", category, league="nba", quotes={"x": k})
    pe = Event("polymarket", "P", "p title", category, league="nba", quotes={"x": p})
    pair = Pair("x", k, p, 1.0)
    return Match(ke, pe, 1.0, [pair]), pair


def test_fill_walks_levels():
    levels = [Level(0.40, 100), Level(0.42, 200)]
    cost, fills = fill(levels, 150)
    assert cost == pytest.approx(40 + 50 * 0.42)
    assert fills == [(0.40, 100), (0.42, 50)]
    assert fill(levels, 301) is None


def test_fair_value_leans_to_deeper_tighter_venue():
    k = quote("kalshi", 0.49, 0.51, 1_000_000)
    p = quote("polymarket", 0.39, 0.41, 1_000)
    fair, share = fair_value(k, p, Config())
    assert share > 0.9 and fair > 0.49
    # flip volumes: Kalshi now barely matters
    k.volume_24h_usd, p.volume_24h_usd = 1_000, 1_000_000
    fair, share = fair_value(k, p, Config())
    assert share < 0.1 and fair < 0.42


def test_hedged_arb_sized_by_depth_and_net_of_fees():
    cfg = Config(mode="arb")
    # Kalshi YES asks 0.40x100, 0.42x200. Poly NO asks 0.55x150, 0.57x500.
    k = quote("kalshi", 0.38, 0.40, 100_000, yes_asks=[Level(0.40, 100), Level(0.42, 200)])
    p = quote("polymarket", 0.45, 0.46, 100_000, no_asks=[Level(0.55, 150), Level(0.57, 500)])
    m, pair = match_for(k, p)
    [s] = Evaluator(cfg, NOW).evaluate(m, pair)
    assert s.kind == "hedged_arb" and s.contracts == 150
    # cost: kalshi 40 + 21 = 61, poly 82.5; kalshi fees 1.68 + ceil(0.8526) = 0.86
    assert s.total_fees == pytest.approx(2.54)
    assert s.expected_profit == pytest.approx(150 - 61 - 82.5 - 2.54)
    assert [l.action for l in s.legs] == ["BUY NO", "BUY YES"]  # no side names in this synthetic quote
    assert s.legs[1].limit_price == 0.42 and s.sized_from_book


def test_no_arb_when_fees_eat_the_edge():
    cfg = Config(mode="arb")
    # 0.41 + 0.57 = 0.98 but Kalshi fee ~0.017 -> 0.003 edge < 0.01
    k = quote("kalshi", 0.43, 0.47, 100_000)
    p = quote("polymarket", 0.39, 0.41, 100_000)
    m, pair = match_for(k, p)
    assert Evaluator(cfg, NOW).evaluate(m, pair) == []


def test_stake_cap_respected():
    cfg = Config(mode="arb", max_stake_usd=50)
    k = quote("kalshi", 0.38, 0.40, 1_000_000, yes_asks=[Level(0.40, 10_000)])
    p = quote("polymarket", 0.45, 0.46, 1_000_000, no_asks=[Level(0.55, 10_000)])
    m, pair = match_for(k, p)
    [s] = Evaluator(cfg, NOW).evaluate(m, pair)
    assert s.total_cost <= 50 and s.contracts >= 50


def test_signal_uses_kalshi_as_reference():
    cfg = Config(mode="signal", min_signal_edge=0.03)
    k = quote("kalshi", 0.54, 0.56, 500_000)  # tight, heavy
    p = quote("polymarket", 0.44, 0.46, 5_000, yes_asks=[Level(0.46, 1_000)])
    m, pair = match_for(k, p)
    [s] = Evaluator(cfg, NOW).evaluate(m, pair)
    assert s.kind == "signal" and s.legs[0].action == "BUY YES"
    assert s.fair_prob > 0.5 and s.kalshi_weight > 0.8
    assert s.volume_ratio == pytest.approx(0.01)
    assert s.price_ratio == pytest.approx(0.46 / 0.56)
    # fractional Kelly: 0.25 * 1000 * (fair - .46) / .54, below the $250 max stake
    assert s.total_cost < 250


def test_signal_suppressed_when_kalshi_is_the_thin_venue():
    cfg = Config(mode="signal", min_kalshi_volume_24h_usd=0)
    k = quote("kalshi", 0.54, 0.56, 1_000)
    p = quote("polymarket", 0.44, 0.46, 5_000_000)
    m, pair = match_for(k, p)
    assert Evaluator(cfg, NOW).evaluate(m, pair) == []


def test_tail_prices_ignored():
    cfg = Config()
    k = quote("kalshi", 0.05, 0.06, 100_000)
    p = quote("polymarket", 0.01, 0.02, 100_000)
    m, pair = match_for(k, p)
    assert all(l.avg_price >= cfg.min_price for s in Evaluator(cfg, NOW).evaluate(m, pair) for l in s.legs)


def test_large_divergence_is_treated_as_mismatch():
    # 40c vs 1c on a "matched" pair: different questions, not an arb
    k = quote("kalshi", 0.39, 0.41, 100_000)
    p = quote("polymarket", 0.005, 0.01, 100_000)
    m, pair = match_for(k, p)
    ev = Evaluator(Config(min_price=0.0), NOW)
    assert not ev.screen(pair) and ev.evaluate(m, pair) == []


def test_long_dated_arb_rejected_below_min_apr():
    k = quote("kalshi", 0.38, 0.40, 100_000)
    p = quote("polymarket", 0.45, 0.46, 100_000)
    k.close_time = p.close_time = datetime(2028, 11, 7, tzinfo=timezone.utc)  # ~2 years out
    m, pair = match_for(k, p)
    assert Evaluator(Config(mode="arb"), NOW).evaluate(m, pair) == []
    assert Evaluator(Config(mode="arb", min_arb_apr=0), NOW).evaluate(m, pair)
