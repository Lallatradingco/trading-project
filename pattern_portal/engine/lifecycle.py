"""Turn shape candidates into patterns with a status, levels and an outcome.

Status rules (all on closing prices):
  Forming   - shape is complete, no close beyond the breakout level yet.
  Marginal  - closed beyond the level, but not decisively (less than the
              decisive margin), or the decisive close is today's candle.
  Confirmed - a decisive close beyond the level that then held.
  Failed    - broke out decisively, then closed back through the level within
              the hold window.
A close through the opposite side before any breakout invalidates the shape
(it is dropped, not reported), as does waiting too long without a breakout.
"""

from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from .detectors import (Ctx, cups_and_curves, double_top_bottom, flags_pennants,
                        head_and_shoulders, trendline_patterns, triple_top_bottom)
from .model import Candidate, Line
from .pivots import atr_pct, multi_scale_pivots, zigzag

HOLD_BARS = 10          # a breakout must hold this long to stay Confirmed
MAX_WAIT = {"1D": 60, "1W": 26, "1M": 12}   # longest wait for a breakout after the shape completes
CHART_POINTS = 160      # max closes stored per pattern for the mini chart


def quality_label(score: float) -> Optional[str]:
    if score >= 0.8:
        return "Textbook"
    if score >= 0.65:
        return "Strong"
    if score >= 0.5:
        return "Fair"
    return None


def _decisive(atrp: float) -> float:
    return max(0.005, 0.35 * atrp)


def evaluate(cand: Candidate, ctx: Ctx, volume: Optional[np.ndarray]) -> Optional[dict]:
    c, n = ctx.close, ctx.n
    bull = cand.up if cand.bias in ("bull", "neutral") else None
    bear = cand.dn if cand.bias in ("bear", "neutral") else None
    inval: Optional[tuple[Line, int]] = None
    if cand.bias == "bull" and cand.dn is not None:
        inval = (cand.dn, -1)
    elif cand.bias == "bear" and cand.up is not None:
        inval = (cand.up, 1)

    bo = level = None
    sgn = 0
    marg = None
    expiry = min(cand.expiry, cand.end + MAX_WAIT.get(ctx.tf, 60))
    for t in range(cand.end + 1, n):
        if t > expiry:
            return None
        hit = None
        if bull is not None and c[t] > bull.at(t):
            hit = (1, bull.at(t))
        elif bear is not None and c[t] < bear.at(t):
            hit = (-1, bear.at(t))
        if hit is None:
            if inval is not None:
                line, side = inval
                if side * (c[t] - line.at(t)) > 0:
                    return None
            marg = None
            continue
        s, L = hit
        if s * (c[t] - L) / L >= _decisive(ctx.atrp[t]):
            bo, level, sgn = t, L, s
            break
        marg = (t, L, s)

    status = "Forming"
    if bo is None:
        if n - 1 > expiry:
            return None
        if marg is not None and marg[0] == n - 1:
            status = "Marginal"
            _, level, sgn = marg
    else:
        status = "Confirmed"
        d = _decisive(ctx.atrp[bo])
        for t in range(bo + 1, min(n, bo + 1 + HOLD_BARS)):
            if sgn * (c[t] - level) / level < -0.25 * d:
                status = "Failed"
                break
        if status == "Confirmed" and n - 1 - bo < 2:
            status = "Marginal"            # needs two closes after the break to count as held

    direction = cand.bias if sgn == 0 else ("bull" if sgn == 1 else "bear")
    # Levels: where a close completes the pattern, the measured target, the stop.
    if direction == "bull":
        entry = level if level is not None else cand.up.at(n - 1 if bo is None else bo)
        target = cand.target_up if cand.target_up is not None else entry + cand.height_up
        stop = cand.stop_up
    elif direction == "bear":
        entry = level if level is not None else cand.dn.at(n - 1 if bo is None else bo)
        target = cand.target_dn if cand.target_dn is not None else entry - cand.height_dn
        stop = cand.stop_dn
    else:
        entry = target = stop = None
    if entry is not None and entry <= 0:
        return None                      # a sloped line projected below zero: not a usable level
    if target is not None and target <= 0:
        target = None
    # A stop taken from an old swing can sit on the wrong side of a sloped
    # breakout line; fall back to the opposite boundary, then to half the height.
    if entry is not None and stop is not None and sgn_dir(direction) * (entry - stop) <= 0:
        x = n - 1 if bo is None else bo
        opp = cand.dn if direction == "bull" else cand.up
        height = cand.height_up if direction == "bull" else cand.height_dn
        alt = opp.at(x) if opp is not None else None
        if alt is not None and sgn_dir(direction) * (entry - alt) > 0:
            stop = alt
        else:
            stop = entry - sgn_dir(direction) * 0.5 * height
    if stop is not None and stop <= 0:
        stop = None
    rr = None
    if entry is not None and target is not None and stop is not None:
        risk = abs(entry - stop)
        if risk > 0 and (target - entry) * (entry - stop) > 0:
            rr = abs(target - entry) / risk

    outcome = None
    if status == "Confirmed" and target is not None and stop is not None:
        outcome = "Open"
        for t in range(bo + 1, n):
            if sgn * (c[t] - target) >= 0:
                outcome = "Target hit"
                break
            if sgn * (c[t] - stop) <= 0:
                outcome = "Stopped"
                break

    vol_ok = None
    if bo is not None and volume is not None and bo >= 20:
        base = float(np.nanmean(volume[bo - 20:bo]))
        vol_ok = bool(base > 0 and volume[bo] >= 1.5 * base)

    # Lines to draw: breakout boundaries run from the shape to the break (or today).
    segs = list(cand.segments)
    x0 = cand.line_x0 if cand.line_x0 is not None else cand.start
    x_end = bo if bo is not None else n - 1
    for line, kind, lx0 in ((cand.up, "upper", cand.up_x0), (cand.dn, "lower", cand.dn_x0)):
        if line is None:
            continue
        is_break = (line is bull) or (line is bear)
        if not is_break and not cand.other_is_boundary:
            continue                      # an invalidation level, not part of the drawing
        if not is_break:
            kind = "bound"
        elif line.m == 0 and cand.pattern != "Rectangle":
            kind = "neck" if cand.family != "Curve & Cup" else "rim"
        xs = lx0 if lx0 is not None else x0
        segs.append({"kind": kind, "x1": int(xs), "y1": float(line.at(xs)),
                     "x2": int(x_end), "y2": float(line.at(x_end))})

    event = bo if bo is not None else cand.end
    span = cand.end - cand.start + 1
    w0 = max(0, cand.start - max(8, span // 4))
    if n - 1 - event <= 2 * span + 20:
        w1 = n - 1
    else:
        w1 = min(n - 1, event + max(15, span // 2))
    step = max(1, math.ceil((w1 - w0 + 1) / CHART_POINTS))
    # sample forward from w0 and always include w1, so both the pattern's first
    # bar and the latest close are on the chart; x holds each sample's bar index
    xs = list(range(w0, w1 + 1, step))
    if xs[-1] != w1:
        xs.append(w1)
    closes = c[xs]

    # live range levels only for shapes that haven't picked a direction yet
    lv_up = cand.up.at(n - 1) if (direction == "neutral" and cand.up is not None) else None
    lv_dn = cand.dn.at(n - 1) if (direction == "neutral" and cand.dn is not None) else None
    if direction == "neutral" and any(v is not None and v <= 0 for v in (lv_up, lv_dn)):
        return None

    return sanitize({
        "pattern": cand.pattern,
        "family": cand.family,
        "direction": direction,
        "status": status,
        "score": round(cand.score * 100),
        "quality": quality_label(cand.score),
        "start": cand.start,
        "end": cand.end,
        "breakout_idx": bo,
        "event_idx": event,
        "span": span,
        "breakout": _r(entry),
        "target": _r(target),
        "stop": _r(stop),
        "rr": None if rr is None else round(rr, 1),
        "range_up": _r(lv_up),
        "range_dn": _r(lv_dn),
        "outcome": outcome,
        "volume_confirmed": vol_ok,
        "points": [[int(i), round(float(p), 2), lab] for i, p, lab in cand.points],
        "segments": [{**s, "y1": round(s["y1"], 2), "y2": round(s["y2"], 2)} for s in segs],
        "chart": {"i0": int(w0), "step": int(step), "x": [int(v) for v in xs],
                  "c": [round(float(v), 2) for v in closes]},
        "scale": cand.scale,
    })


def sanitize(r: dict) -> dict:
    """After rounding to paise, drop a stop that equals the entry and a target at or below zero."""
    if r["breakout"] is not None and r["stop"] is not None and r["stop"] == r["breakout"]:
        r["stop"], r["rr"], r["outcome"] = None, None, None
    if r["target"] is not None and r["target"] <= 0:
        r["target"], r["rr"], r["outcome"] = None, None, None
    return r


def sgn_dir(direction: str) -> int:
    return 1 if direction == "bull" else -1


def _r(v):
    return None if v is None or not np.isfinite(v) else round(float(v), 2)


def _iou(a: dict, b: dict) -> float:
    lo, hi = max(a["start"], b["start"]), min(a["end"], b["end"])
    inter = max(0, hi - lo + 1)
    union = max(a["end"], b["end"]) - min(a["start"], b["start"]) + 1
    return inter / union


def dedupe(found: list[dict]) -> list[dict]:
    """Same pattern found at several sizes/windows: keep the best-drawn one."""
    kept: list[dict] = []
    for p in sorted(found, key=lambda r: -r["score"]):
        if any(k["pattern"] == p["pattern"] and _iou(k, p) > 0.5 for k in kept):
            continue
        kept.append(p)
    return sorted(kept, key=lambda r: r["event_idx"])


def analyze(df: pd.DataFrame, tf: str = "1D") -> list[dict]:
    """Every pattern in a price history (oldest first). Needs Close; High/Low/Volume optional."""
    df = df.dropna(subset=["Close"])
    if len(df) < 60:
        return []
    ctx = Ctx(close=df["Close"].to_numpy(float), atrp=atr_pct(df), tf=tf)
    vol = df["Volume"].to_numpy(float) if "Volume" in df.columns else None
    piv = multi_scale_pivots(df)
    cands: list[Candidate] = []
    scales = sorted(piv)
    for sc in scales:
        p = piv[sc]
        cands += double_top_bottom(ctx, p, sc)
        cands += triple_top_bottom(ctx, p, sc)
        cands += head_and_shoulders(ctx, p, sc)
        cands += trendline_patterns(ctx, p, sc)
    if tf in ("1D", "1W"):
        # flags need finer swings: the pole tip only retraces a little
        fine = zigzag(ctx.close, np.maximum(ctx.atrp * 1.2, 0.015))
        cands += flags_pennants(ctx, fine, 1.2)
    rims = {}
    for sc in scales[1:]:
        for pv in piv[sc]:
            rims.setdefault(pv.idx, pv)
    cands += cups_and_curves(ctx, sorted(rims.values(), key=lambda q: q.idx), scales[1])

    found = []
    for cd in cands:
        if quality_label(cd.score) is None:
            continue
        r = evaluate(cd, ctx, vol)
        if r is not None:
            found.append(r)
    out = dedupe(found)
    dates = df.index
    for r in out:
        r["tf"] = tf
        r["start_date"] = _d(dates[r["start"]])
        r["end_date"] = _d(dates[r["end"]])
        r["event_date"] = _d(dates[r["event_idx"]])
        r["breakout_date"] = _d(dates[r["breakout_idx"]]) if r["breakout_idx"] is not None else None
        r["bars_ago"] = len(df) - 1 - r["event_idx"]
        r["chart"]["d0"] = _d(dates[r["chart"]["i0"]])
        r["chart"]["d1"] = _d(dates[r["chart"]["x"][-1]])
    return out


def _d(ts) -> str:
    return pd.Timestamp(ts).strftime("%Y-%m-%d")
