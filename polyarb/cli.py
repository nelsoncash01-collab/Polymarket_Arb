"""Command line entry point: `python -m polyarb --help`."""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
import time
from dataclasses import asdict

from polyarb.config import Config
from polyarb.http import HttpFetcher, ReplayFetcher
from polyarb.scanner import ScanResult, scan
from polyarb.strategy import Suggestion


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def _num(x: float | None, fmt: str = ".3f") -> str:
    return "-" if x is None else format(x, fmt)


def render(s: Suggestion) -> str:
    head = "HEDGED ARB" if s.kind == "hedged_arb" else "SIGNAL"
    lines = [
        f"[{head}] {s.proposition}  ({s.league or s.category})",
        f"  kalshi : {s.kalshi_event}",
        f"  poly   : {s.poly_event}",
    ]
    for leg in s.legs:
        lines.append(
            f"  -> {leg.venue:10} {leg.action:18} {leg.contracts:>6} @ avg {leg.avg_price:.3f} "
            f"(limit {leg.limit_price:.3f})  cost ${leg.cost:,.2f} + fee ${leg.fee:,.2f}  [{leg.order_ref}]"
        )
    lines.append(
        f"  profit ${s.expected_profit:,.2f} on ${s.total_cost:,.2f}  edge/ct {s.edge_per_contract:.3f}  "
        f"ROI {_pct(s.roi)}  APR {_pct(s.apr)}  days {_num(s.days_to_close, '.1f')}"
    )
    lines.append(
        f"  kalshi mid {_num(s.kalshi_mid)}  poly mid {_num(s.poly_mid)}  fair {_num(s.fair_prob)}  "
        f"price ratio {_num(s.price_ratio)}  volume ratio {_num(s.volume_ratio, '.2f')}  "
        f"kalshi wt {_pct(s.kalshi_weight)}  conf {s.confidence:.2f}  match {s.match_score:.2f}"
    )
    for w in s.warnings:
        lines.append(f"  ! {w}")
    return "\n".join(lines)


def _write_outputs(result: ScanResult, args) -> None:
    rows = [asdict(s) for s in result.suggestions]
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(rows, fh, indent=2, default=str)
    if args.csv:
        with open(args.csv, "w", newline="") as fh:
            flat = [{k: v for k, v in r.items() if k not in ("legs", "warnings")} |
                    {"legs": " | ".join(f"{l['action']} {l['venue']} {l['contracts']}@{l['limit_price']}"
                                        for l in r["legs"]),
                     "warnings": "; ".join(r["warnings"])} for r in rows]
            if flat:
                writer = csv.DictWriter(fh, fieldnames=list(flat[0]))
                writer.writeheader()
                writer.writerows(flat)


def run_once(cfg: Config, args) -> ScanResult:
    fetcher = ReplayFetcher(args.replay) if args.replay else HttpFetcher(record_dir=args.record)
    result = scan(cfg, fetcher, sports=not args.politics_only, politics=not args.sports_only)
    if args.show_matches:
        print(f"== {len(result.matches)} matched events ==")
        for m in result.matches:
            print(f"  {m.score:.2f}  {m.kalshi.event_id:<40} <-> {m.poly.event_id}")
            for pair in m.pairs:
                print(f"         {pair.kalshi.label!r} <-> {pair.poly.label!r}  "
                      f"k {_num(pair.kalshi.mid())} p {_num(pair.poly.mid())}")
    for err in result.errors:
        print(f"error: {err}", file=sys.stderr)
    arbs = sum(s.kind == "hedged_arb" for s in result.suggestions)
    print(f"== {len(result.matches)} matched events, {arbs} hedged arbs, "
          f"{len(result.suggestions) - arbs} signals ==")
    for s in result.suggestions[: args.top]:
        print(render(s))
        print()
    _write_outputs(result, args)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="polyarb", description=__doc__)
    ap.add_argument("--config", help="JSON file overriding polyarb.config.Config fields")
    scope = ap.add_mutually_exclusive_group()
    scope.add_argument("--sports-only", action="store_true")
    scope.add_argument("--politics-only", action="store_true")
    ap.add_argument("--leagues", help="comma list, e.g. nfl,nba,mlb")
    ap.add_argument("--mode", choices=["arb", "signal", "both"], help="which suggestions to emit")
    ap.add_argument("--no-books", action="store_true", help="skip order-book fetches (top-of-book sizing)")
    ap.add_argument("--bankroll", type=float)
    ap.add_argument("--max-stake", type=float)
    ap.add_argument("--min-arb-edge", type=float)
    ap.add_argument("--min-signal-edge", type=float)
    ap.add_argument("--top", type=int, default=25, help="how many suggestions to print")
    ap.add_argument("--show-matches", action="store_true", help="print every matched pair (audit matching)")
    ap.add_argument("--json", help="write suggestions to this JSON file")
    ap.add_argument("--csv", help="write suggestions to this CSV file")
    ap.add_argument("--record", help="save raw API responses to this dir (for replay/backtests)")
    ap.add_argument("--replay", help="read API responses from a --record dir instead of the network")
    ap.add_argument("--watch", type=float, help="rescan every N seconds")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING,
                        format="%(levelname)s %(name)s: %(message)s")
    cfg = Config.load(args.config)
    if args.leagues:
        cfg.leagues = [x.strip().lower() for x in args.leagues.split(",") if x.strip()]
    if args.mode:
        cfg.mode = args.mode
    if args.no_books:
        cfg.fetch_books = False
    for flag, attr in (("bankroll", "bankroll_usd"), ("max_stake", "max_stake_usd"),
                       ("min_arb_edge", "min_arb_edge"), ("min_signal_edge", "min_signal_edge")):
        if getattr(args, flag) is not None:
            setattr(cfg, attr, getattr(args, flag))

    if not args.watch:
        run_once(cfg, args)
        return 0
    while True:
        print(time.strftime("---- %Y-%m-%d %H:%M:%S ----"))
        try:
            run_once(cfg, args)
        except Exception as exc:  # noqa: BLE001 - keep watching through transient failures
            print(f"scan failed: {exc}", file=sys.stderr)
        time.sleep(args.watch)
