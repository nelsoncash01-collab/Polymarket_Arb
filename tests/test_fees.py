import pytest

from polyarb.fees import KalshiFees, PolymarketFees, ceil_cents


def test_kalshi_taker_fee_rounds_up_to_cent():
    # 0.07 * 100 * 0.4 * 0.6 = 1.68 exactly: no spurious extra cent
    assert KalshiFees().cost(100, 0.40) == pytest.approx(1.68)
    # 0.07 * 1 * 0.5 * 0.5 = 0.0175 -> 0.02
    assert KalshiFees().cost(1, 0.50) == pytest.approx(0.02)
    assert KalshiFees().cost(0, 0.50) == 0


def test_kalshi_series_override():
    fees = KalshiFees(series_rates={"KXINX": 0.035})
    assert fees.rate_for("KXINX-26SEP24-B5000") == 0.035
    assert fees.rate_for("KXNFLGAME-26SEP24KCBUF-KC") == 0.07


def test_polymarket_fee_curves():
    assert PolymarketFees().cost(100, 0.5) == 0
    f = PolymarketFees()
    # per-market schedule, conservative "pq" shape: 100 * 0.05 * 0.25
    assert f.cost(100, 0.5, rate=0.05, exponent=1) == pytest.approx(1.25)
    assert PolymarketFees(formula="p_pq").cost(100, 0.5, rate=0.05, exponent=1) == pytest.approx(0.625)
    assert f.cost(100, 0.5, rate=0.0) == 0  # fees disabled
    # "pq" is never below "p_pq"
    for p in (0.1, 0.5, 0.9):
        assert f.cost(10, p, 0.04, 1) >= PolymarketFees(formula="p_pq").cost(10, p, 0.04, 1)


def test_ceil_cents():
    assert ceil_cents(0.0100000001) == 0.01
    assert ceil_cents(0.0101) == 0.02
