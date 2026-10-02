"""Stock-level conviction (bullish vs bearish) and next-session intraday picks.

Conviction weighs every *live* pattern on a stock:

    weight = shape quality x status x timeframe x recency x reliability x volume

* status       Confirmed 1.0, Marginal 0.8, Forming 0.55 (scaled down the further
               price still is from the breakout level). A recent *failed*
               breakout counts 0.6 towards the opposite side (busted patterns
               tend to run the other way).
* timeframe    daily 1.0, weekly 1.6, monthly 2.2 (bigger patterns, more weight)
* recency      exp(-candles since the event / tau), tau = 10 daily, 4 weekly, 2 monthly
               (three times longer for shapes that haven't broken out yet)
* reliability  how often this pattern type on this timeframe reached its target
               before its stop across the whole scanned market (shrunk towards
               50% when there are few cases), relative to a coin flip
* volume       x1.15 when the breakout came on 1.5x average volume

Trend context adds a smaller share: close above/below the 50- and 200-day
averages, 50 above/below 200, and the 20-day return.

Ratio = (bull + 0.5) / (bull + bear + 1), a neutral prior so that thin evidence
doesn't read as certainty. It summarises the chart evidence; it is not a
probability of profit.
"""

from __future__ import annotations

import math
from collections import defaultdict

import numpy as np
import pandas as pd

TF_W = {"1D": 1.0, "1W": 1.6, "1M": 2.2}
TAU = {"1D": 10.0, "1W": 4.0, "1M": 2.0}
WINDOW = {"1D": 30, "1W": 12, "1M": 6}          # candles a signal stays live
STATUS_W = {"Confirmed": 1.0, "Marginal": 0.8, "Forming": 0.55}
FAILED_W = 0.6
TREND_W = 0.6                                     # full trend agreement ~ one solid pattern
PRIOR_N = 8                                       # shrinkage strength for reliability
PRIOR_EVIDENCE = 0.5                              # neutral weight added to each side of the ratio


# ------------------------------------------------------------------ stock metrics

def stock_metrics(df: pd.DataFrame) -> dict:
    c = df["Close"].astype(float)
    h, l = df["High"].astype(float), df["Low"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    atr = float(tr.rolling(14).mean().iloc[-1]) if len(df) >= 15 else float("nan")
    sma50 = float(c.rolling(50).mean().iloc[-1]) if len(c) >= 50 else None
    sma200 = float(c.rolling(200).mean().iloc[-1]) if len(c) >= 200 else None
    vol = df["Volume"].astype(float)
    turnover = float((c * vol).tail(60).median()) if len(c) >= 20 else 0.0
    v5 = float(vol.tail(5).mean())
    v50 = float(vol.tail(50).mean()) if len(vol) >= 50 else float("nan")
    ret20 = float(c.iloc[-1] / c.iloc[-21] - 1) if len(c) > 21 else 0.0
    hi52 = float(c.tail(250).max())
    lo52 = float(c.tail(250).min())
    return {
        "atr": _r(atr), "atr_pct": _r(100 * atr / c.iloc[-1]) if atr == atr else None,
        "sma50": _r(sma50), "sma200": _r(sma200),
        "turnover_cr": round(turnover / 1e7, 2),          # median daily traded value, Rs crore
        "vol_ratio": round(v5 / v50, 2) if v50 and v50 == v50 and v50 > 0 else None,
        "ret20": round(100 * ret20, 1), "hi52": _r(hi52), "lo52": _r(lo52),
    }


def _r(v):
    return None if v is None or (isinstance(v, float) and not math.isfinite(v)) else round(float(v), 2)


# ------------------------------------------------------------------ reliability

def reliability(patterns: list[dict]) -> dict:
    """Target-before-stop rate per (pattern, timeframe) across the whole scan.

    A breakout that failed straight back counts as a loss, an open trade is ignored.
    """
    wins = defaultdict(int)
    losses = defaultdict(int)
    r_sum = defaultdict(float)
    for p in patterns:
        if p["direction"] == "neutral" and p["status"] != "Failed" and p.get("outcome") is None:
            continue
        key = f"{p['pattern']}|{p['tf']}"
        rr = p.get("rr")
        rr = float(rr) if rr is not None and rr == rr else None
        if p["status"] == "Failed":
            losses[key] += 1
            r_sum[key] -= 1.0
        elif p.get("outcome") == "Target hit":
            wins[key] += 1
            r_sum[key] += rr if rr else 1.0
        elif p.get("outcome") == "Stopped":
            losses[key] += 1
            r_sum[key] -= 1.0
    tw, tn = sum(wins.values()), sum(wins.values()) + sum(losses.values())
    base = tw / tn if tn else 0.5
    out = {"__all__": {"wins": tw, "cases": tn, "rate": round(base, 3), "adj": round(base, 3),
                       "exp_r": round(sum(r_sum.values()) / tn, 2) if tn else None}}
    for key in set(wins) | set(losses):
        w, lo = wins[key], losses[key]
        n = w + lo
        out[key] = {"wins": w, "cases": n, "rate": round(w / n, 3) if n else None,
                    "adj": round((w + PRIOR_N * base) / (n + PRIOR_N), 3),
                    "exp_r": round(r_sum[key] / n, 2) if n else None}
    return out


def _rel(p: dict, rel: dict) -> float:
    """Reliability relative to the market-wide average for all patterns."""
    base = rel.get("__all__", {}).get("rate") or 0.5
    r = rel.get(f"{p['pattern']}|{p['tf']}")
    adj = r["adj"] if r else base
    return float(np.clip(adj / base, 0.4, 1.8))


# ------------------------------------------------------------------ conviction

def is_live(p: dict) -> bool:
    if p["status"] in ("Forming", "Marginal"):
        return True                      # still unresolved (expired shapes are already dropped)
    if p["bars_ago"] > WINDOW[p["tf"]]:
        return False
    if p["status"] == "Confirmed":
        return p.get("outcome") in (None, "Open")
    return p["status"] == "Failed"


def pattern_weight(p: dict, rel: dict) -> tuple[str, float]:
    """(side, weight) a live pattern adds to a stock's conviction."""
    if p["direction"] == "neutral":
        return "none", 0.0
    # an unresolved shape is still current, so it fades three times slower than a breakout
    tau = TAU[p["tf"]] * (3 if p["status"] in ("Forming", "Marginal") else 1)
    rec = math.exp(-p["bars_ago"] / tau)
    base = (p["score"] / 100) * TF_W[p["tf"]] * rec * _rel(p, rel)
    if p.get("volume_confirmed"):
        base *= 1.15
    if p["status"] == "Failed":
        return ("bear" if p["direction"] == "bull" else "bull"), base * FAILED_W
    if p["status"] in ("Forming", "Marginal"):
        # an unbroken shape far from its breakout level is weak evidence so far
        vs = p.get("vs_breakout")
        vs = abs(float(vs)) if vs is not None and vs == vs else 0.0
        base *= max(0.2, 1 - vs / 20)
    return p["direction"], base * STATUS_W[p["status"]]


def conviction(patterns: list[dict], metrics: dict, last_close: float, rel: dict) -> dict:
    bull = bear = 0.0
    drivers = []
    for p in patterns:
        if not is_live(p):
            continue
        side, w = pattern_weight(p, rel)
        if w <= 0:
            continue
        if side == "bull":
            bull += w
        else:
            bear += w
        drivers.append({"id": p["id"], "pattern": p["pattern"], "tf": p["tf"], "status": p["status"],
                        "side": side, "weight": round(w, 3)})
    checks = []
    s50, s200 = metrics.get("sma50"), metrics.get("sma200")
    if s50:
        checks.append({"check": "Close above 50-day average", "pass": last_close > s50})
    if s200:
        checks.append({"check": "Close above 200-day average", "pass": last_close > s200})
    if s50 and s200:
        checks.append({"check": "50-day average above 200-day", "pass": s50 > s200})
    checks.append({"check": "Up over the last 20 sessions", "pass": (metrics.get("ret20") or 0) > 0})
    t = sum(1 if ch["pass"] else -1 for ch in checks)
    n_checks = max(1, len(checks))
    bull_t = TREND_W * max(0, t) / n_checks
    bear_t = TREND_W * max(0, -t) / n_checks
    B, S = bull + bull_t, bear + bear_t
    total = B + S
    # A neutral prior on both sides keeps thin evidence from reading as certainty.
    pct = round(100 * (B + PRIOR_EVIDENCE) / (total + 2 * PRIOR_EVIDENCE))
    label = ("Strong bullish" if pct >= 75 else "Bullish" if pct >= 60 else
             "Mixed" if pct > 40 else "Bearish" if pct > 25 else "Strong bearish")
    if bull + bear == 0:
        label = "Trend only: " + ("bullish" if pct > 50 else "bearish" if pct < 50 else "flat")
    strength = "High" if total >= 2.0 else "Medium" if total >= 0.8 else "Low"
    drivers.sort(key=lambda d: -d["weight"])
    return {
        "bull_pct": pct, "label": label,
        "bull": round(B, 3), "bear": round(S, 3),
        "pattern_bull": round(bull, 3), "pattern_bear": round(bear, 3),
        "trend_bull": round(bull_t, 3), "trend_bear": round(bear_t, 3),
        "evidence": round(total, 2), "strength": strength,
        "drivers": drivers[:8], "trend": checks,
    }


# ------------------------------------------------------------------ intraday picks

MIN_TURNOVER_CR = 5.0      # intraday needs liquidity: median daily value traded >= Rs 5 crore


def intraday_candidates(sym: str, patterns: list[dict], metrics: dict, last_close: float,
                        conv: dict, rel: dict) -> list[dict]:
    """Daily setups that can trigger in the next session, with ATR-sized levels."""
    atr = metrics.get("atr")
    if not atr or atr <= 0 or (metrics.get("turnover_cr") or 0) < MIN_TURNOVER_CR:
        return []
    out = []
    for p in patterns:
        if p["tf"] != "1D" or p["bars_ago"] > WINDOW["1D"]:
            continue
        kind = None
        if p["status"] in ("Forming", "Marginal"):
            if p["direction"] == "neutral":
                up, dn = p.get("range_up"), p.get("range_dn")
                if up is None or dn is None:
                    continue
                side, level = ("bull", up) if (up - last_close) <= (last_close - dn) else ("bear", dn)
            else:
                side, level = p["direction"], p["breakout"]
            if level is None:
                continue
            dist = (level - last_close) / atr if side == "bull" else (last_close - level) / atr
            if dist < -0.5 or dist > 1.0:
                continue                  # already well past, or more than a day's range away
            kind = "Breakout trigger"
            if dist < 0:                  # already through the level: entry is the current price
                kind, level = "Just broke out", last_close
        elif p["status"] == "Confirmed" and p["bars_ago"] <= 3 and p.get("outcome") == "Open":
            side, level = p["direction"], p["breakout"]
            dist = 0.0
            kind = "Fresh breakout follow-through"
            level = last_close            # continuation entry is the current price area
        else:
            continue
        s = 1 if side == "bull" else -1
        entry = float(level)
        stop = entry - s * 0.5 * atr
        if p.get("stop") is not None and s * (entry - p["stop"]) > 0 and abs(entry - p["stop"]) < 0.5 * atr:
            stop = p["stop"]
        target = entry + s * 1.0 * atr
        if p.get("target") is not None and s * (p["target"] - entry) > 0 and abs(p["target"] - entry) < atr:
            target = p["target"]
        rr = abs(target - entry) / abs(entry - stop) if entry != stop else None
        prox = 1 - min(1.0, abs(dist))
        rel_r = _rel(p, rel) / 1.8
        align = (conv["bull_pct"] if side == "bull" else 100 - conv["bull_pct"]) / 100
        vr = metrics.get("vol_ratio") or 1.0
        vol_s = min(1.0, max(0.0, (vr - 0.6) / 1.0))
        score = 100 * (0.30 * prox + 0.20 * p["score"] / 100 + 0.20 * rel_r
                       + 0.20 * align + 0.10 * vol_s)
        out.append({
            "symbol": sym, "id": p["id"], "side": side, "kind": kind, "pattern": p["pattern"],
            "status": p["status"], "quality": p["quality"],
            "entry": round(entry, 2), "stop": round(stop, 2), "target": round(target, 2),
            "rr": round(rr, 1) if rr else None, "dist_atr": round(dist, 2),
            "last_close": round(last_close, 2), "atr": atr,
            "reliability": rel.get(f"{p['pattern']}|1D"), "conviction": conv["bull_pct"],
            "conviction_label": conv["label"], "vol_ratio": metrics.get("vol_ratio"),
            "turnover_cr": metrics.get("turnover_cr"), "score": round(score, 1),
        })
    return out
