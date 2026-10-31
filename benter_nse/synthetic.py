"""Fake NSE-like data with a small planted edge, to test the engine end to end.

`python -m benter_nse demo` builds this in data_demo/ and runs the full
pipeline, so the engine can be checked without a Kotak key. The planted edge
(mild 12-month momentum plus one-month reversal) is deliberately weak, like a
real one; the engine should find it, and should report NO EDGE when run with
--no-signal.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

INDUSTRIES = ["Financial Services", "Information Technology", "Healthcare", "Metals & Mining",
              "Power", "Fast Moving Consumer Goods", "Automobile and Auto Components",
              "Capital Goods", "Chemicals", "Oil Gas & Consumable Fuels", "Realty",
              "Consumer Services"]


def make(out_dir: Path, n_stocks: int = 150, years: int = 8, seed: int = 7,
         signal: bool = True, end: date | None = None) -> None:
    rng = np.random.default_rng(seed)
    end = pd.Timestamp(end or date.today())
    days = pd.bdate_range(end=end, periods=int(252 * years))
    T, N = len(days), n_stocks

    idio = rng.uniform(0.012, 0.032, N)
    beta = rng.uniform(0.6, 1.4, N)
    mkt = rng.normal(0.0004, 0.010, T)
    logp = np.zeros((T, N))
    logp[0] = np.log(rng.uniform(80, 3000, N))
    a_mom, a_rev = (0.0012, 0.0008) if signal else (0.0, 0.0)
    for t in range(1, T):
        alpha = np.zeros(N)
        if t > 260:
            mom = logp[t - 22] - logp[t - 253]
            rev = logp[t - 1] - logp[t - 22]
            zm = (mom.argsort().argsort() / (N - 1)) - 0.5
            zr = (rev.argsort().argsort() / (N - 1)) - 0.5
            alpha = a_mom * 2 * zm - a_rev * 2 * zr
        # Fat-ish tails: Student-t shocks.
        shock = rng.standard_t(5, N) * idio / np.sqrt(5 / 3)
        logp[t] = logp[t - 1] + beta * mkt[t] + alpha + shock - 0.5 * idio**2
    close = np.exp(logp)

    base_vol = rng.lognormal(np.log(3e5), 0.6, N)
    base_vol[:6] = 1.5e4  # a few illiquid names the liquidity filter should drop

    prices = out_dir / "prices"
    prices.mkdir(parents=True, exist_ok=True)
    rows = []
    for i in range(N):
        sym = f"STK{i:03d}"
        c = close[:, i]
        o = np.concatenate([[c[0]], c[:-1] * np.exp(rng.normal(0, idio[i] / 3, T - 1))])
        hi = np.maximum(o, c) * (1 + np.abs(rng.normal(0, idio[i] / 2, T)))
        lo = np.minimum(o, c) * (1 - np.abs(rng.normal(0, idio[i] / 2, T)))
        v = base_vol[i] * rng.lognormal(0, 0.4, T)
        df = pd.DataFrame({"date": days, "open": o, "high": hi, "low": lo, "close": c,
                           "volume": v.round()})
        if i == 10:  # raw prices before a 1:1 bonus are twice as high
            k = T // 2
            df.loc[: k - 1, ["open", "high", "low", "close"]] *= 2
            df.loc[: k - 1, "volume"] /= 2
        if i == 11:  # a genuine 38% crash gap that must NOT be treated as a split
            k = T // 3
            df.loc[k:, ["open", "high", "low", "close"]] *= 0.62
        df.round({"open": 2, "high": 2, "low": 2, "close": 2}).to_csv(prices / f"{sym}.csv", index=False, date_format="%Y-%m-%d")
        rows.append({"Company Name": f"Synthetic {i:03d} Ltd", "Industry": INDUSTRIES[i % len(INDUSTRIES)],
                     "Symbol": sym, "Series": "EQ", "ISIN Code": f"INE{i:06d}01"})
    pd.DataFrame(rows).to_csv(out_dir / "ind_nifty500list.csv", index=False)
