# Pattern Scanner portal

A standalone chart-pattern screener for NSE stocks. It finds 23 classic patterns on
daily, weekly and monthly candles, tracks each one from *forming* to *confirmed* or
*failed*, and shows them in a dark card-grid portal with filters, mini charts,
breakout / target / stop levels and a per-stock pattern history.

It runs as its **own app on its own port (8765)** and does not touch Lalla Hub or
any of its portals. Integration into Lalla Hub can come later.

## Start

```bash
./pattern_portal/run.sh                      # first run: Nifty 500 download + scan, then serve
UNIVERSE=all ./pattern_portal/run.sh         # or every NSE stock with 2+ years of history
```

Then open <http://127.0.0.1:8765>. Or step by step:

```bash
pip install -r pattern_portal/requirements.txt
python -m pattern_portal setup --universe nifty500   # nifty50|nifty100|nifty200|fno|nifty500|all
python -m pattern_portal serve                        # --port 8765 --host 127.0.0.1
python -m pattern_portal refresh                      # update prices + rescan (e.g. daily after 4 pm)
```

**Scan again** in the portal does the same as `refresh`. First download of all
stocks is roughly 1 GB of history; after that only the last few KB per stock are
fetched. Data lives in `pattern_data/` (git-ignored).

## Data

- History: [eod2_data](https://github.com/BennyThadikaran/eod2_data), NSE bhavcopy
  daily candles adjusted for splits and bonuses, refreshed weekly.
- Latest days: Yahoo Finance via `yfinance` (optional). Only completed sessions
  are added; today's candle is used after 3:45 pm IST.
- Index lists (Nifty 50/100/200/500, F&O) and company names from niftyindices.com
  and NSE archives, cached in `pattern_data/meta/`.

## Patterns

| Family | Patterns |
|---|---|
| Reversal | Double Top/Bottom, Triple Top/Bottom, Head & Shoulders, Inverse H&S, Rising/Falling Wedge |
| Continuation | Ascending/Descending/Symmetrical Triangle, Bull/Bear Flag, Bull/Bear Pennant |
| Range | Rectangle, Ascending/Descending Channel, Broadening Formation |
| Curve & Cup | Cup & Handle, Inverted Cup & Handle, Rounding Bottom, Rounding Top |

How it works (`engine/`):

1. **Swings** – ZigZag on closing prices with a reversal threshold of 2.5×, 4.5× and
   8× the stock's own ATR%, so small and large patterns are both found. Flags use a
   finer 1.2× pass.
2. **Shapes** – geometric rules on those swings (equal tops, head above shoulders,
   trendline fits with ≥5 touches, quadratic fit for cups, pole + tight
   consolidation for flags). Each gets a 0–100 *shape quality*: Textbook ≥80,
   Strong ≥65, Fair ≥50; below 50 is dropped.
3. **Lifecycle** – *Forming* until a close crosses the level; *Marginal* if the
   close is beyond it but not decisively (less than max(0.5%, 0.35×ATR%)) or the
   break is today's candle; *Confirmed* once a decisive close holds; *Failed* if it
   closes back through the level within 10 candles. A close through the opposite
   side before any breakout invalidates the shape (it is not shown).
4. **Levels** – target is the classic measured move (pattern height or flag pole
   from the breakout; wedges target their starting extreme); stop is the last
   opposite swing. History rows also record whether target or stop came first.

Results are pattern-recognition output, not trade advice.

## Conviction ratio

Every stock gets a bullish share of its chart evidence (`engine/conviction.py`):

- Each live pattern (forming, marginal, confirmed with the trade still open, or a
  failed breakout in the last 30 daily / 12 weekly / 6 monthly candles) adds
  `quality × status × timeframe × recency × measured hit rate × volume`.
  Failed breakouts count towards the opposite side. Unbroken shapes count less
  the further price still is from the breakout level.
- The hit rate is measured on the scan itself: how often that pattern type on
  that timeframe reached its target before its stop across all NSE stocks
  (a failed breakout counts as a loss), shrunk towards the market average when
  cases are few.
- Trend adds a smaller share: close vs 50/200-day averages, 50 vs 200, 20-day return.
- Ratio = (bull + 0.5) / (bull + bear + 1). 75%+ strong bullish, 60%+ bullish,
  40–60 mixed, 25–40 bearish, under 25 strong bearish. Evidence strength
  (low/medium/high) says how much weight sits behind the reading.

## Intraday picks

A next-session watchlist from daily patterns (there is no intraday feed):
liquid stocks (₹5 cr+ median daily value) whose daily pattern trigger is within
one ATR of the last close, or which broke out in the last three sessions.
Stop = 0.5 ATR, target = 1 ATR (or the pattern's own levels when tighter), so
R:R is about 1:2. Ranked by trigger proximity (30%), shape quality (20%),
measured hit rate (20%), agreement with the stock's conviction (20%) and
volume pick-up (10%).

## Hosted read-only copy

`python -m pattern_portal export OUT` writes `OUT/index.html` and `OUT/data/`
(recent patterns with charts, full light history, conviction, picks). The same
front end runs on those files with filtering done in the browser, so it can be
hosted anywhere static.

## Layout

```
pattern_portal/
  engine/       pivots.py  detectors.py  lifecycle.py  model.py
  data.py       download / cache / resample candles
  universe.py   index lists and company names
  scanner.py    parallel scan -> pattern_data/scan/current/
  store.py      filtering, facets, sorting for the API
  server.py     Flask app (own port) + background "Scan again" job
  static/       index.html, app.css, app.js (no build step)
tests/          synthetic-pattern tests: python -m pytest tests
```

API: `GET /api/intraday`, `GET /api/stocks`, `GET /api/lists`, `GET /api/patterns` (filters: universe, q, family, direction, tf, status,
quality, within, volume, sort, page), `GET /api/stock/<SYMBOL>`, `GET /api/meta`,
`POST /api/scan`. These are what a later Lalla Hub integration would call.
