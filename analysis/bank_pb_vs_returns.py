"""Commercial bank price-to-book vs. subsequent returns, quarterly since 1996.

Prices/returns come from Yahoo Finance (yfinance). Yahoo only exposes ~5-7
quarters of balance-sheet history, so book value per share (BVPS) before that
must be supplied via --bv-csv (columns: ticker,date,bvps), e.g. from 10-Q/10-K
filings, FDIC call reports aggregated to the holding company, or Compustat.
BVPS must be on the same split basis as Yahoo's split-adjusted Close.

Regression (pooled panel): forward return over the next h quarters ~ P/B at quarter end.

Usage:
    pip install yfinance pandas numpy matplotlib
    python bank_pb_vs_returns.py --bv-csv bvps.csv --horizon 4
"""
import argparse

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import yfinance as yf

BANKS = ["JPM", "BAC", "WFC", "C", "USB", "PNC", "TFC", "KEY", "FITB",
         "MTB", "RF", "HBAN", "CMA", "ZION", "BK", "STT"]


def quarterly_prices(tickers, start):
    raw = yf.download(tickers, start=start, interval="1mo",
                      auto_adjust=False, progress=False, group_by="column")
    q = lambda col: raw[col].resample("QE").last()
    return q("Close"), q("Adj Close")  # Close -> P/B, Adj Close -> total return


def yahoo_bvps(tickers):
    rows = []
    for t in tickers:
        bs = yf.Ticker(t).quarterly_balance_sheet
        if bs is None or bs.empty:
            continue
        eq = bs.loc["Stockholders Equity"] if "Stockholders Equity" in bs.index else None
        sh = bs.loc["Ordinary Shares Number"] if "Ordinary Shares Number" in bs.index else None
        if eq is None or sh is None:
            continue
        for d, v in (eq / sh).dropna().items():
            rows.append((t, pd.Timestamp(d), v))
    return pd.DataFrame(rows, columns=["ticker", "date", "bvps"])


def load_bvps(tickers, csv_path):
    bv = yahoo_bvps(tickers)
    if csv_path:
        ext = pd.read_csv(csv_path, parse_dates=["date"])
        bv = pd.concat([ext, bv]).drop_duplicates(["ticker", "date"], keep="first")
    bv["q"] = bv["date"] + pd.offsets.QuarterEnd(0)
    return bv.pivot_table(index="q", columns="ticker", values="bvps", aggfunc="last")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="1996-01-01")
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
