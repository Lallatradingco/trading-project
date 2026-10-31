"""When to bet and how much — Benter's bar and fractional Kelly for stocks.

A pick is treated as a two-outcome bet that is fair at the crowd's odds pi:
if the stock beats the median stock it gains a = 2m(1 - pi) on the average
stock, otherwise it loses l = 2m*pi, where m is its typical move against the
market. At c = pi the bet is worth exactly zero, before costs. A round trip
costs k either way, so with the combined probability c:

    expected profit  = c*(a - k) - (1 - c)*(l + k) = 2m*(c - pi) - k
    worth taking only when   c > pi + k / (2m)          (the "bar")

This is the stock version of p > pi / (1 - t): costs play the role of the
track's takeout, and you must beat the crowd's odds by enough to pay them.
Kelly for the same two-outcome bet:

    full Kelly stake  x* = c / (l + k) - (1 - c) / (a - k)

Stock bets have small swings relative to the stake, so full Kelly is huge and
very sensitive to errors in c. The engine bets a fraction of it and caps each
stock and the total.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def bar(pi: pd.Series, m: pd.Series, cost: float) -> pd.Series:
    return pi + cost / (2 * m)


def full_kelly(c: pd.Series, pi: pd.Series, m: pd.Series, cost: float) -> pd.Series:
    win = 2 * m * (1 - pi) - cost
    lose = 2 * m * pi + cost
    x = c / lose - (1 - c) / win.clip(lower=1e-6)
    return x.where(win > 0, 0.0).clip(lower=0.0)


def size(df: pd.DataFrame, cost: float, kelly_fraction: float, max_weight: float,
         max_gross: float, min_weight: float = 0.0) -> pd.DataFrame:
    """Add bar, edge, kelly, weight and a plain-language call to one date's rows."""
    out = df.copy()
    out["bar"] = bar(out["pi"], out["m"], cost)
    out["edge"] = out["c"] - out["bar"]
    out["kelly_full"] = full_kelly(out["c"], out["pi"], out["m"], cost).where(out["edge"] > 0, 0.0)
    w = (out["kelly_full"] * kelly_fraction).clip(upper=max_weight)
    gross = w.sum()
    if gross > max_gross:
        w = w * (max_gross / gross)
    out["weight"] = w.where(w >= min_weight, 0.0) if min_weight > 0 else w
    out["exp_net"] = 2 * out["m"] * (out["c"] - out["pi"]) - cost
    out["call"] = np.where(out["weight"] > 0, "BET", "no bet")
    return out


def rebalance_dates(dates: pd.Series, horizon: int, every: int) -> list[pd.Timestamp]:
    """Non-overlapping holding periods: every ceil(horizon/every)-th sample date."""
    uniq = sorted(pd.Series(dates).unique())
    step = int(np.ceil(horizon / every))
    return [pd.Timestamp(d) for d in uniq[::step]]


def backtest(rows: pd.DataFrame, horizon: int, every: int, cost: float,
             kelly_fraction: float, max_weight: float, max_gross: float,
             min_weight: float = 0.0) -> dict:
    """Out-of-sample test of the betting rule, net of costs.

    Returns are measured against the average NSE 500 stock (what a version
    hedged with an equal-weight basket would earn), per ₹1 of capital.
    "Naive" buys the model's top 10% each period with no crowd check and no
    cost bar — the comparison Benter's second stage is meant to win.
    """
    data = rows[rows["c"].notna() & rows["y"].notna()]
    periods = []
    for d in rebalance_dates(data["date"], horizon, every):
        day = data[data["date"] == d]
        if day.empty:
            continue
        sized = size(day, cost, kelly_fraction, max_weight, max_gross, min_weight)
        picks = sized[sized["weight"] > 0]
        net = picks["rel_ret"] - cost
        top = day[day["f"] >= day["f"].quantile(0.9)]
        top_net = top["rel_ret"] - cost
        periods.append({
            "date": d,
            "picks": int(len(picks)),
            "gross": float(picks["weight"].sum()),
            "ret_kelly": float((picks["weight"] * net).sum()),
            "hits": int((net > 0).sum()),
            "naive_picks": int(len(top)),
            "ret_naive": float(top_net.mean()) if len(top) else 0.0,
            "naive_hits": int((top_net > 0).sum()),
        })
    p = pd.DataFrame(periods)
    if p.empty:
        return {"periods": p, "kelly": {}, "naive": {}}

    def stats(ret: pd.Series, n_picks: pd.Series, hits: pd.Series) -> dict:
        r = ret.fillna(0.0)
        sd = r.std(ddof=1)
        return {
            "periods": int(len(r)),
            "avg_picks": float(n_picks.mean()),
            "hit_rate": float(hits.sum() / n_picks.sum()) if n_picks.sum() else float("nan"),
            "avg_period_ret": float(r.mean()),
            "t_stat": float(r.mean() / (sd / np.sqrt(len(r)))) if sd > 0 and len(r) > 1 else float("nan"),
            "pct_positive": float((r > 0).mean()),
            "worst_period": float(r.min()),
            "growth_of_100": float(100 * np.prod(1 + r)),
            "years": float(len(r) * horizon / 252),
        }

    p["eq_kelly"] = 100 * np.cumprod(1 + p["ret_kelly"].fillna(0))
    p["eq_naive"] = 100 * np.cumprod(1 + p["ret_naive"].fillna(0))
    return {
        "periods": p,
        "kelly": stats(p["ret_kelly"], p["picks"], p["hits"]),
        "naive": stats(p["ret_naive"], p["naive_picks"], p["naive_hits"]),
    }


def verdict(summary: dict, bt: dict) -> tuple[str, str]:
    """Plain-language answer to: does the model beat the crowd after costs?"""
    gain = summary.get("r2_gain_over_crowd", float("nan"))
    k = bt.get("kelly", {})
    t = k.get("t_stat", float("nan"))
    avg = k.get("avg_period_ret", float("nan"))
    if not np.isfinite(gain) or gain <= 0:
        return ("NO EDGE",
                "Out of sample, the model's odds were no better than the fair-price "
                "baseline. Don't trade it: this is the second stage doing its job.")
    if not np.isfinite(avg) or avg <= 0:
        return ("NO EDGE AFTER COSTS",
                "The model knows a little the price doesn't, but not enough to clear "
                "trading costs. Don't trade it.")
    if not np.isfinite(t) or t < 2:
        return ("WEAK EDGE",
                "Positive after costs out of sample, but not clearly beyond luck "
                f"(t-stat {t:.1f}; 2 or more is the usual bar). Paper-trade first.")
    return ("EDGE",
            f"Beat the fair-price baseline and stayed positive after costs out of sample "
            f"(t-stat {t:.1f}). Still start small: past edges shrink.")
