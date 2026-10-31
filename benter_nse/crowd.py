"""The crowd's odds and the payoff of a bet.

Horse racing hands Benter the crowd's probability in the tote odds. For a
stock, the crowd's view *is* the price. If prices are fair, every stock has
the same expected return, so its chance of beating the median stock is about
50/50, nudged down for very jumpy stocks: big swings drag down compounded
returns, so their typical (median) outcome sits a little lower.

    log return over h days  ~  N( mu*h - s^2/2 ,  s^2 ),   s = daily vol * sqrt(h)
    P(beat the median stock) =  Phi( (avg s^2 - s_i^2) / (2 * s_i) )

The model has to beat this baseline, just as Benter's had to beat the tote.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

SQRT_2_OVER_PI = np.sqrt(2 / np.pi)


def crowd_prob(df: pd.DataFrame, horizon: int) -> pd.Series:
    s2 = df["vol_60"].clip(lower=1e-4) ** 2 * horizon
    avg = s2.groupby(df["date"]).transform("mean")
    return pd.Series(norm.cdf((avg - s2) / (2 * np.sqrt(s2))), index=df.index).clip(0.05, 0.95)


def payoff_scale(df: pd.DataFrame, horizon: int) -> pd.Series:
    """Typical size of the stock's move against the market over the horizon.

    Used to turn a probability into rupees (see betting.py). For a normal
    spread with standard deviation sd, the average absolute move is
    sd * sqrt(2/pi).
    """
    return (df["relvol_60"].clip(lower=1e-4) * np.sqrt(horizon) * SQRT_2_OVER_PI).rename("m")
