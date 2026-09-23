# Polymarket_Arb

Scans **Kalshi** and **Polymarket** for the same sports games and elections,
then suggests Polymarket trades, using Kalshi's odds as the reference. It
weighs price ratios, volume ratios and each venue's fees.

It only suggests trades. It never places orders.

## Quick start

```bash
pip install -r requirements.txt
python -m polyarb --show-matches                 # sports + elections, default config
python -m polyarb --sports-only --leagues nfl,mlb --mode arb
python -m polyarb --politics-only --json out.json --csv out.csv
python -m polyarb --watch 60                     # rescan every minute
python -m pytest -q                              # offline test suite
```

Everything it reads is public market data, so you don't need API keys.

## What it suggests

| kind | trade | risk |
|---|---|---|
| `hedged_arb` | Buy one side on Polymarket and the **opposite** side on Kalshi | Locked in, *if both venues resolve the same way* |
| `signal` | Buy one side on Polymarket only, when it is cheap against a fair value weighted toward Kalshi | Directional: you lose if the outcome goes the other way |

`--mode arb|signal|both` (default `both`).

### Factors

* **Price ratio**: the Polymarket ask divided by the Kalshi ask for the same side. Below 1 means Polymarket is cheaper. This column is for reading; edge (below) decides the trade.
* **Volume ratio**: Polymarket 24h $ divided by Kalshi 24h $. This sets how much Kalshi counts in the fair value:
  `weight = volume_24h ^ 0.5 / spread` for each venue, and the fair value is the weighted average of the two mids.
  If Polymarket trades 20× more than Kalshi, Kalshi hardly moves the fair value and signals rarely fire. That is on purpose: a thin market is not a good oracle. Volume also caps size: at most 10% of the thinner venue's 24h volume.
* **Fees**:
  * Kalshi taker fee: `ceil_to_cent(0.07 × C × P × (1−P))`, charged per fill level. Per-series overrides go in `kalshi_fees.series_rates`.
  * Polymarket fee: `C × P × rate × (P(1−P))^exponent`. The default rate is **0**. Markets marked `feesEnabled: false` are always 0.
  * **Check current fee schedules before trading.** Polymarket has been adding fees to more market types. A 1–3¢ edge is exactly what a fee change wipes out.
* **Edge**: net $ per contract after fees.
  * Arb: `1 − ask_poly − ask_kalshi − fees`, required to be ≥ `min_arb_edge` (1¢).
  * Signal: `fair − ask − fee`, required to be ≥ `min_signal_edge` (3¢) and to meet a confidence floor.
* **Sizing**:
  * Real order books are walked level by level: Kalshi `/orderbook` (bids only, so asks are derived as `1 − opposite bid`) and Polymarket CLOB `/book`.
  * Size stops where the next contract would fall below the edge threshold, at `max_stake_usd`, or at the volume cap.
  * Signals are also capped by fractional Kelly (`kelly_fraction × bankroll × f*`).

### Matching

* **Sports** (NFL, NBA, MLB, NHL, WNBA; NCAAF with fuzzy names): a match needs the same league, the same two teams after alias resolution, and a date within ±1 day.
  * Alias resolution maps "Kansas City" / "Chiefs" / "KC" to `nfl:KC`, "Los Angeles C" to `nfl:LAC`, and so on.
  * An ambiguous case, like a doubleheader, is skipped rather than guessed.
  * 3-way (draw) markets are excluded.
* **Elections**: titles must share the same *discriminating keys* (state, office, year, district, primary/nominee/runoff, country). Outcomes are then paired by party (`Democratic party` ↔ `Democrat`) or by candidate surname plus first initial.
  * "West Virginia Senate" never matches "Virginia Senate", and a nominee market never matches a general-election market.
  * For anything the heuristic gets wrong, use `force_pairs` / `block_pairs` in the config.
  * Always run with `--show-matches` and check the pairs.
* On a hedge, the Kalshi leg uses the **same market's NO**, not the other team's YES. If an NFL game ends in a tie, "KC NO" still pays, whereas "BUF YES" would not.

## Record / replay

`--record DIR` saves every raw API response, and `--replay DIR` re-runs a scan from those files with no network. Use them to build a history for backtesting thresholds, or to debug a bad match after the fact.

## Caveats

1. **Resolution risk is the real risk in "risk-free" arbs.** Venues can settle differently: media call vs. certification, candidate withdrawal, overtime and tie rules, postponed games. Every suggestion prints the warnings that apply. Read the rules for anything politics-related.
2. **Top-of-book sizing is a guess.** If a book fetch fails, the suggestion is labelled `sized from top-of-book only`, is ranked lower, and should be treated as an alert, not an order.
3. **The API schemas are handled defensively but were not verified live in this build.** The build sandbox could not reach either API. Kalshi's cent fields and its newer `*_dollars` fields are both supported. On the first real run, use `--show-matches -v` to confirm parsing.
4. **Capital is locked until both legs settle.** A 2% arb that locks capital for 40 days works out to about 18% APR, so the report shows ROI and APR.
5. **Pure Polymarket signals assume Kalshi is the better-informed venue.** When Kalshi is the thinner venue, the model gives it little weight on purpose.

## Layout

```
polyarb/
  config.py      all thresholds (JSON-overridable)
  fees.py        Kalshi + Polymarket fee models
  kalshi.py      Kalshi v2 client + parsers
  polymarket.py  Gamma (discovery) + CLOB (books) client + parsers
  teams.py       team alias tables
  matching.py    sports + election matching
  strategy.py    fair value, book walking, arb/signal evaluation
  scanner.py     pipeline: fetch → match → screen → books → evaluate → rank
  cli.py         command line
tests/           offline tests with API-shaped fixtures
```
