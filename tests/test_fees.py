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


def test_polymarket_fee_default_zero_and_curve():
    assert PolymarketFees().cost(100, 0.5) == 0
    f = PolymarketFees(taker_rate=0.25, exponent=1)
    assert f.cost(100, 0.5) == pytest.approx(100 * 0.5 * 0.25 * 0.25)
    assert f.cost(100, 0.5, rate=0.0) == 0  # market with fees disabled


def test_ceil_cents():
    assert ceil_cents(0.0100000001) == 0.01
    assert ceil_cents(0.0101) == 0.02
