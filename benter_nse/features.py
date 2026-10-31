"""Turn price panels into one row per (date, stock): factors, crowd prior, outcome.

Every factor is ranked against the other stocks on the same date and mapped
to a normal score, the stock-market version of Benter standardising each
factor within a race.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from .data import Panel

TRADING_YEAR = 252

# Ranked (cross-sectional) factors fed to the stage-1 model.
RANKED = [
    "mom_12_1",       # 12-month return, skipping the last month
    "mom_6_1",        # 6-month return, skipping the last month
    "rev_1m",         # last month's return (short-term reversal)
    "rev_1w",         # last week's return
    "sector_rel_3m",  # 3-month return minus the industry median
    "vol_60",         # daily volatility, last 60 days
    "max_1m",         # biggest one-day jump in the last month
    "dist_52wh",      # distance below the 52-week high
    "ma200_gap",      # distance from the 200-day average
    "ma50_gap",       # distance from the 50-day average
    "rsi_14",         # 14-day RSI
    "vol_surge",      # 20-day vs 120-day average volume
    "turnover",       # median daily traded value (size / liquidity)
]
# Yes/no flags used as they are.
FLAGS = ["bull_div"]  # RSI <= 25 bullish divergence seen in the last 5 days
FEATURES = [f"z_{c}" for c in RANKED] + FLAGS


def _rsi(close: pd.DataFrame, n: int = 14) -> pd.DataFrame:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def sample_dates(index: pd.DatetimeIndex, every: int) -> pd.DatetimeIndex:
    """Every `every`-th trading day, counted back from the latest so it is always included."""
    pos = np.arange(len(index) - 1, -1, -every)[::-1]
    return index[pos]


def build(panel: Panel, industry: pd.Series, horizon: int, every: int,
          min_turnover_cr: float) -> pd.DataFrame:
    c, h, v = panel.close, panel.high, panel.volume
    logc = np.log(c)
    r1 = logc.diff()
    rel_r1 = r1.sub(r1.median(axis=1), axis=0)
    vol = v.where(v > 0)
    rsi = _rsi(c)
    turnover_rs = (c * v).rolling(60, min_periods=40).median()

    wide = {
        "mom_12_1": logc.shift(21) - logc.shift(TRADING_YEAR),
        "mom_6_1": logc.shift(21) - logc.shift(126),
        "mom_3": logc - logc.shift(63),
        "rev_1m": logc - logc.shift(21),
        "rev_1w": logc - logc.shift(5),
        "vol_60": r1.rolling(60, min_periods=40).std(),
        "relvol_60": rel_r1.rolling(60, min_periods=40).std(),
        "max_1m": r1.rolling(21, min_periods=15).max(),
        "dist_52wh": logc - np.log(h.rolling(TRADING_YEAR, min_periods=200).max()),
        "ma200_gap": logc - np.log(c.rolling(200, min_periods=180).mean()),
        "ma50_gap": logc - np.log(c.rolling(50, min_periods=45).mean()),
        "rsi_14": rsi,
        "vol_surge": np.log(vol.rolling(20, min_periods=15).mean()
                            / vol.rolling(120, min_periods=90).mean()),
        "turnover": np.log(turnover_rs),
        "turnover_cr": turnover_rs / 1e7,
        "bull_div": (((rsi <= 25) & (c < c.shift(5)) & (rsi > rsi.shift(5))
                      & (v > v.rolling(5).mean())).astype(float)
                     .rolling(5, min_periods=1).max()),
        "fwd": logc.shift(-horizon) - logc,
        "close": c,
    }

    dates = sample_dates(c.index, every)
    long = pd.concat(
        {k: w.loc[dates].stack(future_stack=True) for k, w in wide.items()}, axis=1
    )
    long.index.names = ["date", "symbol"]
    long = long.reset_index()
    long["industry"] = long["symbol"].map(industry).fillna("Unknown")

    long["eligible"] = (
        long["close"].notna()
        & long["mom_12_1"].notna()
        & long["vol_60"].notna()
        & (long["turnover_cr"] >= min_turnover_cr)
    )
    long = long[long["eligible"]].copy()

    long["sector_rel_3m"] = long["mom_3"] - long.groupby(["date", "industry"])["mom_3"].transform("median")

    # Rank each factor within its date and map to a normal score.
    g = long.groupby("date")
    n = g["symbol"].transform("count").to_numpy()
    for col in RANKED:
        rk = g[col].rank(method="average").to_numpy()
        cnt = g[col].transform("count").to_numpy()
        u = (rk - 0.5) / np.where(cnt > 0, cnt, 1)
        long[f"z_{col}"] = np.where(np.isnan(rk), 0.0, norm.ppf(np.clip(u, 1e-4, 1 - 1e-4)))
    long["bull_div"] = long["bull_div"].fillna(0.0)
    long["n_in_race"] = n

    # Outcome: did the stock beat the median eligible stock over the horizon?
    # Profit is measured against the average stock (a basket you could hedge with).
    simple = np.expm1(long["fwd"])
    by_date = simple.groupby(long["date"])
    beat = (simple > by_date.transform("median")).astype(float)
    long["y"] = np.where(long["fwd"].isna(), np.nan, beat)
    long["rel_ret"] = simple - by_date.transform("mean")
    return long.reset_index(drop=True)
