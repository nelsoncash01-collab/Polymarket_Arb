"""Commercial bank price-to-book vs. subsequent returns, quarterly.

Prices/returns come from Yahoo Finance (yfinance). Book value per share comes
from SEC XBRL companyfacts (~2009 onward): common equity = StockholdersEquity
minus PreferredStockValue, divided by CommonStockSharesOutstanding, with shares
restated for later splits so they match Yahoo's split-adjusted Close.
Optional --bv-csv (ticker,date,bvps) overrides/extends that, e.g. pre-2009.

Regression (pooled panel): forward return over the next h quarters ~ P/B at quarter end.

Usage:
    pip install yfinance pandas numpy matplotlib
    python bank_pb_vs_returns.py --horizon 4
"""
import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests
import yfinance as yf

SEC_UA = {"User-Agent": "bank-pb-research admin@example.com"}  # SEC requires a UA with contact

BANKS = ["JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC", "KEY", "FITB",
         "MTB", "RF", "HBAN", "CMA", "ZION", "BK", "STT"]


def quarterly_prices(tickers, start):
    raw = yf.download(tickers, start=start, interval="1mo",
                      auto_adjust=False, progress=False, group_by="column")
    q = lambda col: raw[col].resample("QE").last()
    return q("Close"), q("Adj Close")  # Close -> P/B, Adj Close -> total return


def _sec_series(facts, tag, unit):
    """Quarter-end instant values for a us-gaap tag, latest filing wins."""
    items = facts["facts"].get("us-gaap", {}).get(tag, {}).get("units", {}).get(unit, [])
    df = pd.DataFrame([i for i in items if i.get("form", "").startswith(("10-Q", "10-K"))])
    if df.empty:
        return pd.Series(dtype=float)
    df["q"] = pd.to_datetime(df["end"]) + pd.offsets.QuarterEnd(0)
    return df.sort_values("filed").groupby("q")["val"].last().astype(float)


def sec_bvps(tickers):
    cik = {v["ticker"]: v["cik_str"] for v in
           requests.get("https://www.sec.gov/files/company_tickers.json",
                        headers=SEC_UA, timeout=30).json().values()}
    out = {}
    for t in tickers:
        if t not in cik:
            print(f"skip {t}: no CIK")
            continue
        facts = requests.get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik[t]:010d}.json",
                             headers=SEC_UA, timeout=60).json()
        eq = _sec_series(facts, "StockholdersEquity", "USD")
        pref = _sec_series(facts, "PreferredStockValue", "USD").reindex(eq.index).fillna(0)
        sh = _sec_series(facts, "CommonStockSharesOutstanding", "shares")
        bv = ((eq - pref) / sh).dropna()
        # Restate to Yahoo's split-adjusted basis: divide by every split after the date.
        splits = yf.Ticker(t).splits
        if splits is not None and len(splits):
            splits.index = splits.index.tz_localize(None)
            adj = pd.Series([splits[splits.index > d].prod() for d in bv.index], bv.index)
            bv = bv / adj
        out[t] = bv[bv > 0]
    return pd.DataFrame(out)


def load_bvps(tickers, csv_path):
    bv = sec_bvps(tickers)
    if csv_path:
        ext = pd.read_csv(csv_path, parse_dates=["date"])
        ext["q"] = ext["date"] + pd.offsets.QuarterEnd(0)
        bv = ext.pivot_table(index="q", columns="ticker", values="bvps", aggfunc="last").combine_first(bv)
    return bv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2009-01-01")
    ap.add_argument("--bv-csv", help="historical BVPS: ticker,date,bvps")
    ap.add_argument("--horizon", type=int, default=4, help="forward return horizon, quarters")
    ap.add_argument("--out", default="bank_pb_vs_returns.png")
    args = ap.parse_args()

    close, adj = quarterly_prices(BANKS, args.start)
    bvps = load_bvps(BANKS, args.bv_csv).reindex(close.index)
    pb = close / bvps
    fwd = adj.shift(-args.horizon) / adj - 1

    panel = pd.DataFrame({"pb": pb.stack(), "fwd": fwd.stack()}).dropna()
    panel = panel[(panel.pb > 0) & (panel.pb < 10)]
    if len(panel) < 10:
        raise SystemExit(f"Only {len(panel)} P/B observations - supply --bv-csv for history.")

    x, y = panel.pb.values, panel.fwd.values * 100
    slope, intercept = np.polyfit(x, y, 1)
    resid = y - (slope * x + intercept)
    r2 = 1 - resid.var() / y.var()
    se = np.sqrt(resid.var(ddof=2) / ((x - x.mean()) ** 2).sum())
    q0, q1 = panel.index.get_level_values(0).min(), panel.index.get_level_values(0).max()
    print(f"N={len(panel)}  {q0:%Y-%m} .. {q1:%Y-%m}  horizon={args.horizon}q")
    print(f"fwd_return% = {intercept:.2f} + {slope:.2f} * P/B   R^2={r2:.3f}  t={slope/se:.2f}")
    print("(pooled OLS; overlapping horizons inflate t - treat it as optimistic)")

    fig, (a1, a2) = plt.subplots(1, 2, figsize=(14, 5.5))
    a1.scatter(x, y, s=10, alpha=0.35, color="#2a78d6", edgecolors="none")
    xs = np.linspace(x.min(), x.max(), 50)
    a1.plot(xs, slope * xs + intercept, color="#eb6834", lw=2,
            label=f"OLS: {intercept:.1f} + {slope:.1f}·P/B  (R²={r2:.2f}, t={slope/se:.1f})")
    a1.axhline(0, color="#898781", lw=0.8)
    a1.set(xlabel="Price / book at quarter end",
           ylabel=f"Next {args.horizon}-quarter total return (%)",
           title="Bank P/B vs. forward return")
    a1.legend(frameon=False)

    med = pb.median(axis=1).dropna()
    a2.plot(med.index, med.values, color="#2a78d6", lw=2)
    a2.axhline(1, color="#898781", lw=0.8, ls="--")
    a2.set(title="Median bank P/B by quarter", ylabel="P/B")
    for a in (a1, a2):
        a.spines[["top", "right"]].set_visible(False)
        a.grid(alpha=0.2)
    fig.tight_layout()
    fig.savefig(args.out, dpi=150)
    print(f"saved {args.out}")


if __name__ == "__main__":
    main()
