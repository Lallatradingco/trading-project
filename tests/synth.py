"""Synthetic price paths with known patterns, for testing the detectors."""
import numpy as np
import pandas as pd


def path(knots, noise=0.004, seed=0):
    """Piecewise-linear path through (bar, price) knots plus small noise."""
    rng = np.random.default_rng(seed)
    xs, ys = zip(*knots)
    x = np.arange(xs[-1] + 1)
    base = np.interp(x, xs, ys)
    c = base * (1 + rng.normal(0, noise, len(x)))
    return frame(c, seed)


def frame(c, seed=0):
    rng = np.random.default_rng(seed + 1)
    c = np.asarray(c, float)
    h = c * (1 + np.abs(rng.normal(0, 0.006, len(c))))
    l = c * (1 - np.abs(rng.normal(0, 0.006, len(c))))
    v = rng.integers(80_000, 120_000, len(c)).astype(float)
    idx = pd.bdate_range("2023-01-02", periods=len(c))
    return pd.DataFrame({"Open": c, "High": h, "Low": l, "Close": c, "Volume": v}, index=idx)


def with_breakout_volume(df, at, mult=2.5):
    df = df.copy()
    df.iloc[at, df.columns.get_loc("Volume")] *= mult
    return df


CASES = {
    # prior rise, two equal bottoms, break above the middle peak
    "Double Bottom": [(0, 150), (30, 100), (50, 120), (70, 100.5), (90, 126), (110, 135)],
    "Double Top": [(0, 80), (30, 120), (50, 100), (70, 119.5), (90, 94), (110, 85)],
    "Head & Shoulders": [(0, 80), (25, 115), (40, 100), (60, 130), (80, 101), (100, 114), (118, 92), (130, 85)],
    "Inverse Head & Shoulders": [(0, 140), (25, 105), (40, 120), (60, 90), (80, 119), (100, 106), (118, 128), (130, 135)],
    "Ascending Triangle": [(0, 80), (20, 120), (35, 100), (50, 120), (62, 108), (75, 120), (84, 113), (95, 132)],
    "Descending Triangle": [(0, 140), (20, 100), (35, 125), (50, 100), (62, 115), (75, 100), (84, 107), (95, 88)],
    "Falling Wedge": [(0, 150), (15, 140), (30, 112), (45, 130), (60, 104), (72, 118), (84, 100), (92, 109), (105, 125)],
    "Rectangle": [(0, 80), (20, 120), (35, 100), (50, 120), (65, 100), (80, 120), (95, 100), (110, 119), (122, 135)],
    "Bull Flag": [(0, 100), (30, 102), (40, 125), (46, 121), (50, 123), (55, 119), (59, 121), (63, 118), (70, 132)],
    "Cup & Handle": [(0, 80), (20, 120), (45, 98), (60, 92), (75, 98), (100, 117), (108, 111), (116, 114), (124, 130)],
    "Rounding Top": "curve_top",
}


def case(name, seed=0):
    spec = CASES[name]
    if spec == "curve_top":
        rng = np.random.default_rng(seed)
        x = np.linspace(-1, 1, 140)
        top = 340 + 190 * (1 - x ** 2)
        pre = np.linspace(300, 340, 40)
        post = np.linspace(330, 290, 25)
        c = np.r_[pre, top, post] * (1 + rng.normal(0, 0.004, 205))
        return frame(c, seed)
    return path(spec, seed=seed)
