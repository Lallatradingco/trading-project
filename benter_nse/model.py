"""Benter's two stages, tested walk-forward.

Stage 1 (the "fundamental model"): a logit on the ranked factors gives f, the
model's probability that a stock beats the median stock over the horizon.

Stage 2 (the combination): a second logit starts from logit(pi), the crowd's
fair-price odds, and adds alpha * logit(f), giving c. Benter fitted this
second stage on races the first model had never seen; so does this code. If
alpha (the weight on the model) comes out near zero, the model knows nothing
the price doesn't.

Every probability reported for a year comes from models fitted only on
earlier data, with a gap of one horizon so no training outcome overlaps it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score

from .crowd import crowd_prob, payoff_scale
from .features import FEATURES

log = logging.getLogger(__name__)

LABELS = {
    "z_mom_12_1": "12-month momentum (skip last month)",
    "z_mom_6_1": "6-month momentum (skip last month)",
    "z_rev_1m": "Last month's return",
    "z_rev_1w": "Last week's return",
    "z_sector_rel_3m": "3-month return vs its industry",
    "z_vol_60": "Volatility (60 days)",
    "z_max_1m": "Biggest one-day jump (1 month)",
    "z_dist_52wh": "Closeness to 52-week high",
    "z_ma200_gap": "Distance above 200-day average",
    "z_ma50_gap": "Distance above 50-day average",
    "z_rsi_14": "RSI (14)",
    "z_vol_surge": "Volume surge (20d vs 120d)",
    "z_turnover": "Traded value (size)",
    "bull_div": "RSI bullish divergence (your screener)",
}


def _logit(p: np.ndarray | pd.Series) -> np.ndarray:
    p = np.clip(np.asarray(p, dtype=float), 1e-4, 1 - 1e-4)
    return np.log(p / (1 - p))


def score(y: np.ndarray, p: np.ndarray) -> dict:
    """Benter's yardstick: pseudo-R² against a coin flip, plus AUC."""
    y = np.asarray(y, dtype=float)
    p = np.clip(np.asarray(p, dtype=float), 1e-6, 1 - 1e-6)
    ll = float(np.sum(y * np.log(p) + (1 - y) * np.log(1 - p)))
    ll0 = len(y) * np.log(0.5)
    try:
        auc = float(roc_auc_score(y, p))
    except ValueError:
        auc = float("nan")
    return {"n": int(len(y)), "pseudo_r2": 1 - ll / ll0, "auc": auc}


def _stage1(C: float) -> LogisticRegression:
    return LogisticRegression(C=C, max_iter=2000)


class Stage2:
    """Start from the crowd, lean on the model only as far as it has earned.

        logit(c) = logit(pi) + gamma + alpha * (logit(f) - date average)

    This is Benter's second logit with the crowd's weight held at 1. For a
    horse the crowd's odds vary enormously, so Benter could fit that weight;
    for a stock they barely move from 50/50, so a free weight would just fit
    noise. alpha is the weight the model earns on races it never saw; a
    negative fit is set to 0 (never bet against a model, just ignore it).
    """

    def __init__(self, ridge: float = 1.0):
        self.ridge = ridge
        self.alpha = 0.0
        self.gamma = 0.0

    @staticmethod
    def _parts(df: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        lf = pd.Series(_logit(df["f"]), index=df.index)
        x = (lf - lf.groupby(df["date"]).transform("mean")).to_numpy()
        return x, _logit(df["pi"])

    def fit(self, df: pd.DataFrame, y: pd.Series) -> "Stage2":
        x, off = self._parts(df)
        y = np.asarray(y, dtype=float)
        X = np.column_stack([np.ones_like(x), x])
        w = np.zeros(2)
        for _ in range(50):  # Newton steps with a small ridge for stability
            p = 1 / (1 + np.exp(-(off + X @ w)))
            g = X.T @ (y - p) - self.ridge * w
            H = (X * (p * (1 - p))[:, None]).T @ X + self.ridge * np.eye(2)
            step = np.linalg.solve(H, g)
            w += step
            if np.abs(step).max() < 1e-8:
                break
        self.gamma, self.alpha = float(w[0]), float(max(w[1], 0.0))
        return self

    def predict(self, df: pd.DataFrame) -> np.ndarray:
        x, off = self._parts(df)
        return 1 / (1 + np.exp(-(off + self.gamma + self.alpha * x)))


def _cutoff(start: pd.Timestamp, horizon: int) -> pd.Timestamp:
    # One horizon of trading days, in calendar days, plus a safety margin.
    return start - pd.Timedelta(days=int(np.ceil(horizon * 7 / 5)) + 3)


@dataclass
class Fit:
    rows: pd.DataFrame              # every row with f, pi, m and (where tested) c
    yearly: pd.DataFrame            # one row per tested year
    summary: dict
    coefs: pd.DataFrame             # stage-1 weights of the final model
    stage1: LogisticRegression
    stage2: "Stage2 | None"
    test_years: list[int] = field(default_factory=list)


def walk_forward(df: pd.DataFrame, horizon: int, min_train_years: int, C: float) -> Fit:
    df = df.copy()
    df["pi"] = crowd_prob(df, horizon)
    df["m"] = payoff_scale(df, horizon)
    df["year"] = df["date"].dt.year
    lab = df[df["y"].notna()]
    if lab.empty:
        raise ValueError("No labelled rows: need more price history.")

    first = lab["date"].min()
    years = sorted(lab["year"].unique())
    test_years = [y for y in years
                  if pd.Timestamp(year=int(y), month=1, day=1) - first
                  >= pd.Timedelta(days=int(365 * min_train_years) - 45)]
    if len(test_years) < 2:
        raise ValueError(
            f"Only {len(years)} year(s) of usable history; need at least "
            f"{min_train_years + 2} to train, combine and test. Fetch more years."
        )

    df["f"] = np.nan
    df["c"] = np.nan
    yearly = []
    oos_idx: list[pd.Index] = []
    for j, yr in enumerate(test_years):
        start = pd.Timestamp(year=int(yr), month=1, day=1)
        cut = _cutoff(start, horizon)
        block = df.index[df["year"] == yr]
        train = lab[lab["date"] <= cut]
        m1 = _stage1(C).fit(train[FEATURES], train["y"])
        df.loc[block, "f"] = m1.predict_proba(df.loc[block, FEATURES])[:, 1]

        alpha = np.nan
        if j > 0:
            prev = pd.Index(np.concatenate(oos_idx)) if oos_idx else pd.Index([])
            s2_train = df.loc[prev]
            s2_train = s2_train[s2_train["y"].notna() & (s2_train["date"] <= cut)]
            if len(s2_train) > 200:
                m2 = Stage2().fit(s2_train, s2_train["y"])
                df.loc[block, "c"] = m2.predict(df.loc[block])
                alpha = m2.alpha
        oos_idx.append(block.to_numpy())

        b = df.loc[block]
        b = b[b["y"].notna()]
        row = {"year": int(yr), "train_rows": int(len(train)), "rows": int(len(b)),
               "alpha_model": alpha}
        for name in ["pi", "f", "c"]:
            if b[name].notna().all() and len(b):
                s = score(b["y"], b[name])
                row[f"r2_{name}"], row[f"auc_{name}"] = s["pseudo_r2"], s["auc"]
            else:
                row[f"r2_{name}"] = row[f"auc_{name}"] = np.nan
        yearly.append(row)
        log.info("Year %s: train %d rows, test %d rows", yr, len(train), len(b))

    yearly_df = pd.DataFrame(yearly)

    # Final models for today's signals.
    stage1 = _stage1(C).fit(lab[FEATURES], lab["y"])
    oos = df.loc[np.concatenate(oos_idx)]
    oos = oos[oos["y"].notna()]
    stage2 = Stage2().fit(oos, oos["y"]) if len(oos) > 200 else None

    honest = df[df["c"].notna() & df["y"].notna()]
    summary = {
        "first_sample": str(lab["date"].min().date()),
        "last_labelled": str(lab["date"].max().date()),
        "years_tested": [int(y) for y in test_years],
        "honest_years": [int(y) for y in test_years[1:]],
        "rows_honest": int(len(honest)),
        "alpha_model": stage2.alpha if stage2 is not None else None,
    }
    for name in ["pi", "f", "c"]:
        s = score(honest["y"], honest[name]) if len(honest) else {"pseudo_r2": np.nan, "auc": np.nan}
        summary[f"r2_{name}"] = s["pseudo_r2"]
        summary[f"auc_{name}"] = s["auc"]
    summary["r2_gain_over_crowd"] = summary["r2_c"] - summary["r2_pi"]

    coefs = pd.DataFrame({"feature": FEATURES, "weight": stage1.coef_[0]})
    coefs["label"] = coefs["feature"].map(LABELS)
    coefs = coefs.reindex(coefs["weight"].abs().sort_values(ascending=False).index)

    return Fit(rows=df, yearly=yearly_df, summary=summary, coefs=coefs,
               stage1=stage1, stage2=stage2, test_years=[int(y) for y in test_years])


def predict(df: pd.DataFrame, stage1, stage2, horizon: int) -> pd.DataFrame:
    """Score fresh rows (today's) with fitted models."""
    out = df.copy()
    out["pi"] = crowd_prob(out, horizon)
    out["m"] = payoff_scale(out, horizon)
    out["f"] = stage1.predict_proba(out[FEATURES])[:, 1]
    out["c"] = stage2.predict(out) if stage2 is not None else out["pi"]
    return out
