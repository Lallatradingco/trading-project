"""Swing-point (pivot) detection on closing prices.

Every pattern in this engine is measured on closes, the same way the dashboard
draws them. Swings are found with a ZigZag whose reversal threshold adapts to
each stock's own volatility (a multiple of its ATR%), and the scan runs at
several sizes so that both small and large patterns are found.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class Pivot:
    idx: int          # bar index
    price: float      # close at that bar
    kind: int         # +1 swing high, -1 swing low
    confirmed: bool   # False for the still-developing last extreme


def atr_pct(df: pd.DataFrame, n: int = 14) -> np.ndarray:
    """ATR as a fraction of price, smoothed so the threshold doesn't jitter."""
    c = df["Close"].to_numpy(float)
    if {"High", "Low"} <= set(df.columns):
        h = df["High"].to_numpy(float)
        l = df["Low"].to_numpy(float)
        prev = np.r_[c[0], c[:-1]]
        tr = np.maximum(h - l, np.maximum(np.abs(h - prev), np.abs(l - prev)))
    else:  # close-only data
        tr = np.abs(np.diff(c, prepend=c[0])) * 1.6
    atr = pd.Series(tr).ewm(alpha=1 / n, adjust=False).mean()
    pct = (atr / pd.Series(c)).rolling(50, min_periods=1).median()
    return np.clip(pct.to_numpy(float), 0.004, 0.25)


def zigzag(close: np.ndarray, thr: np.ndarray) -> list[Pivot]:
    """Alternating swing highs/lows; a swing is set once price reverses by thr."""
    n = len(close)
    out: list[Pivot] = []
    if n < 3:
        return out
    hi = lo = close[0]
    hi_i = lo_i = 0
    trend = 0
    ext = ext_i = 0
    for i in range(1, n):
        p = close[i]
        if trend == 0:
            if p > hi:
                hi, hi_i = p, i
            if p < lo:
                lo, lo_i = p, i
            if hi / lo - 1 >= thr[i]:
                if hi_i > lo_i:
                    out.append(Pivot(lo_i, lo, -1, True))
                    trend, ext, ext_i = 1, hi, hi_i
                else:
                    out.append(Pivot(hi_i, hi, 1, True))
                    trend, ext, ext_i = -1, lo, lo_i
            continue
        if trend == 1:
            if p > ext:
                ext, ext_i = p, i
            elif p <= ext * (1 - thr[ext_i]):
                out.append(Pivot(ext_i, ext, 1, True))
                trend, ext, ext_i = -1, p, i
        else:
            if p < ext:
                ext, ext_i = p, i
            elif p >= ext * (1 + thr[ext_i]):
                out.append(Pivot(ext_i, ext, -1, True))
                trend, ext, ext_i = 1, p, i
    if trend != 0 and ext_i > out[-1].idx:
        out.append(Pivot(ext_i, ext, 1 if trend == 1 else -1, False))
    return out


# Reversal threshold as multiples of ATR%: small, medium and large swings.
SCALES = (2.5, 4.5, 8.0)


def multi_scale_pivots(df: pd.DataFrame, scales=SCALES) -> dict[float, list[Pivot]]:
    close = df["Close"].to_numpy(float)
    ap = atr_pct(df)
    res = {}
    for m in scales:
        thr = np.maximum(ap * m, 0.02)
        res[m] = zigzag(close, thr)
    return res
