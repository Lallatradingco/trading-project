# Benter engine for NSE 500

Bill Benter's two-stage method, moved from Hong Kong horse races to NSE 500
stocks. Standalone: its own folder, its own portal on port 8611. Nothing in
Lalla Hub is touched. It produces signals only and cannot place orders.

## The idea in four lines

1. **The race.** Every week, each NSE 500 stock "runs" against the others:
   does it beat the median stock over the next 20 trading days?
2. **The crowd's odds.** For a stock, the crowd's view is the price. If prices
   are fair, each stock has about a 50/50 chance, slightly less for very jumpy
   stocks. The model has to beat that.
3. **Two stages.** A logit on 14 price/volume factors gives the model's odds
   (stage 1). A second logit starts from the crowd's odds and adds the model
   only as far as it has earned on years it never saw (stage 2).
4. **Bet only above the bar, and small.** Costs are the takeout. A stock is a
   bet only when the combined odds beat the crowd's by enough to pay ~0.35%
   round-trip costs. Size = ¼ Kelly, max 5% per stock.

Every number in the report is out of sample: each year is scored by models
fitted only on earlier years, with a one-month gap so no outcome leaks.

## Setup on the Mac

```bash
cd trading-project
pip install -r benter_nse/requirements.txt

# The token from NEO app -> More -> Trade API. No TOTP or MPIN needed:
# history and the scrip master only use this key.
export KOTAK_CONSUMER_KEY="..."
```

## Use

```bash
python -m benter_nse demo          # try it on synthetic data first (no key needed)

python -m benter_nse universe      # NSE 500 list (or save ind_nifty500list.csv into benter_nse/data/)
python -m benter_nse fetch --limit 5   # trial: 5 stocks
python -m benter_nse fetch         # all ~500 stocks, 8 years (first run: roughly an hour)
python -m benter_nse run           # walk-forward test + today's calls + report
python -m benter_nse serve         # http://127.0.0.1:8611
```

Daily, after 4 pm: `fetch` (only new days are pulled), then `signals`.
Weekly or monthly: `run` to retrain and re-test.

Outputs land in `benter_nse/data/output/`: `report.html`, `signals.csv`,
`result.json`, plus the year-by-year test, backtest periods and the
split/bonus check.

## Reading the verdict

| Verdict | Meaning |
|---|---|
| EDGE | Beat the crowd and positive after costs out of sample, t-stat ≥ 2 |
| WEAK EDGE | Positive after costs, but could be luck. Paper-trade first |
| NO EDGE AFTER COSTS | Knows a little the price doesn't, not enough to pay costs |
| NO EDGE | No better than the price. Don't trade it |

A NO EDGE verdict is the engine working. Benter's own test showed a tipster
model that looked accurate on its own but added nothing over the public odds;
the second stage "would have saved that player from losing money".

## Settings

All in `config.toml`: horizon (20 days), costs (35 bps), Kelly fraction
(0.25), caps (5% per stock, 100% total, 0.5% minimum), liquidity filter
(₹5 cr/day), years of history (8).

## Known limits

- **Survivorship bias.** The NSE 500 list is today's; stocks that dropped out
  are missing, so the past looks better than it was.
- **Hedging.** Returns are measured against the average NSE 500 stock. To earn
  that, hedge the market (e.g. Nifty futures); unhedged, you also carry the
  market's move.
- **Kotak data.** 180 days per call; how far back Kotak's history goes is not
  documented. If a split or bonus shows up as an overnight price jump, the
  loader adjusts it and lists it in the report; large gaps that don't match a
  split ratio are left alone and flagged.
- **Factors are price and volume only.** Kotak's API has no fundamentals.

## Files

| File | What it does |
|---|---|
| `kotak.py` | Scrip-master tokens, 180-day chunked daily candles, retries, cache |
| `universe.py` | NSE 500 list |
| `data.py` | Price panels, split/bonus check |
| `features.py` | Factors ranked within each date, the outcome |
| `crowd.py` | Crowd's fair-price odds, typical move size |
| `model.py` | Stage 1, stage 2, walk-forward test |
| `betting.py` | The bar, Kelly sizing, backtest, verdict |
| `engine.py` | Runs the pipeline, writes outputs |
| `report.py`, `portal.py` | HTML report and the Flask portal |
| `synthetic.py` | Fake data with a planted edge, for the demo and tests |

Tests: `python -m pytest benter_nse/tests -q`
