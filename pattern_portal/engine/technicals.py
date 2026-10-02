"""Standard technical indicators for one stock, each with a plain-language reading.

All values are computed on the daily candles (plus a weekly RSI). Each reading
is "bull", "bear" or "neutral"; the summary counts them. These are shown next
to the patterns; they don't change the pattern conviction ratio.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _wilder(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(alpha=1 / n, adjust=False).mean()


def _rsi(c: pd.Series, n: int = 14) -> pd.Series:
    d = c.diff()
    up = _wilder(d.clip(lower=0), n)
    dn = _wilder((-d).clip(lower=0), n)
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def _f(v, nd=2):
    if v is None:
        return None
    v = float(v)
    return round(v, nd) + 0.0 if math.isfinite(v) else None   # + 0.0 turns -0.0 into 0.0


def technicals(df: pd.DataFrame) -> dict:
    c = df["Close"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    v = df["Volume"].astype(float)
    n = len(c)
    last = float(c.iloc[-1])
    out: dict = {"items": []}

    def add(key, label, value, reading, note, unit=""):
        out["items"].append({"key": key, "label": label, "value": value, "unit": unit,
                             "reading": reading, "note": note})

    # --- momentum
    rsi = _rsi(c)
    r = _f(rsi.iloc[-1], 1)
    if r is not None:
        if r >= 70:
            add("rsi", "RSI (14)", r, "bear", "Overbought: momentum stretched, pullback risk")
        elif r <= 30:
            add("rsi", "RSI (14)", r, "bull", "Oversold: selling stretched, bounce possible")
        elif r >= 50:
            add("rsi", "RSI (14)", r, "bull", "Above 50: buyers have the upper hand")
        else:
            add("rsi", "RSI (14)", r, "bear", "Below 50: sellers have the upper hand")
    if n >= 80:
        wk = c.resample("W-FRI").last().dropna()
        if len(wk) >= 20:
            wr = _f(_rsi(wk).iloc[-1], 1)
            add("rsi_w", "Weekly RSI (14)", wr, "bull" if wr >= 50 else "bear",
                "Weekly momentum " + ("positive" if wr >= 50 else "negative"))

    ema12, ema26 = c.ewm(span=12, adjust=False).mean(), c.ewm(span=26, adjust=False).mean()
    macd = ema12 - ema26
    sig = macd.ewm(span=9, adjust=False).mean()
    m, s_ = float(macd.iloc[-1]), float(sig.iloc[-1])
    cross = ""
    if n > 2 and (macd.iloc[-2] - sig.iloc[-2]) * (m - s_) < 0:
        cross = ", crossed in the last session"
    add("macd", "MACD histogram (12, 26, 9)", _f(m - s_), "bull" if m > s_ else "bear",
        ("MACD above its signal line" if m > s_ else "MACD below its signal line")
        + (" and above zero" if m > 0 else " and below zero") + cross)

    if n >= 17:
        ll, hh = l.rolling(14).min(), h.rolling(14).max()
        k = (100 * (c - ll) / (hh - ll).replace(0, np.nan)).rolling(3).mean()
        d = k.rolling(3).mean()
        kv, dv = _f(k.iloc[-1], 1), _f(d.iloc[-1], 1)
        if kv is not None:
            if kv >= 80:
                rd, nt = "bear", "Overbought zone"
            elif kv <= 20:
                rd, nt = "bull", "Oversold zone"
            else:
                rd, nt = ("bull", "%K above %D") if (dv is not None and kv > dv) else ("bear", "%K below %D")
            add("stoch", "Stochastic (14, 3)", kv, rd, nt)

    # --- trend
    for w in (20, 50, 200):
        if n >= w:
            sma = float(c.rolling(w).mean().iloc[-1])
            gap = 100 * (last / sma - 1)
            add(f"sma{w}", f"{w}-day average", _f(sma), "bull" if last > sma else "bear",
                f"Price {abs(gap):.1f}% {'above' if gap >= 0 else 'below'} it", "₹")
    if n >= 200:
        s50, s200 = c.rolling(50).mean(), c.rolling(200).mean()
        up = s50.iloc[-1] > s200.iloc[-1]
        since = ""
        flips = np.nonzero(np.diff(np.sign((s50 - s200).dropna().to_numpy())))[0]
        if len(flips):
            days = len(s50.dropna()) - 1 - flips[-1]
            since = f", crossed {days} sessions ago" if days < 60 else ""
        add("cross", "50 vs 200-day", None, "bull" if up else "bear",
            ("Golden cross: 50-day above 200-day" if up else "Death cross: 50-day below 200-day") + since)

    if n >= 30:
        prev = c.shift(1)
        tr = pd.concat([h - l, (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
        atr = _wilder(tr, 14)
        upm, dnm = h.diff(), -l.diff()
        pdm = pd.Series(np.where((upm > dnm) & (upm > 0), upm, 0.0), index=c.index)
        ndm = pd.Series(np.where((dnm > upm) & (dnm > 0), dnm, 0.0), index=c.index)
        pdi = 100 * _wilder(pdm, 14) / atr
        ndi = 100 * _wilder(ndm, 14) / atr
        dx = 100 * (pdi - ndi).abs() / (pdi + ndi).replace(0, np.nan)
        adx = _wilder(dx.fillna(0), 14)
        a, p_, n_ = _f(adx.iloc[-1], 1), float(pdi.iloc[-1]), float(ndi.iloc[-1])
        if a is not None:
            if a < 20:
                add("adx", "ADX (14)", a, "neutral", "Under 20: no strong trend")
            else:
                add("adx", "ADX (14)", a, "bull" if p_ > n_ else "bear",
                    f"{'Strong' if a >= 25 else 'Building'} {'up' if p_ > n_ else 'down'}trend (+DI {p_:.0f}, -DI {n_:.0f})")
        # Supertrend (10, 3)
        atr10 = _wilder(tr, 10)
        mid = (h + l) / 2
        ub, lb = (mid + 3 * atr10).to_numpy(), (mid - 3 * atr10).to_numpy()
        cc = c.to_numpy()
        fu, fl = ub.copy(), lb.copy()
        trend = np.ones(n)
        for i in range(1, n):
            fu[i] = ub[i] if (ub[i] < fu[i - 1] or cc[i - 1] > fu[i - 1]) else fu[i - 1]
            fl[i] = lb[i] if (lb[i] > fl[i - 1] or cc[i - 1] < fl[i - 1]) else fl[i - 1]
            if trend[i - 1] == 1:
                trend[i] = -1 if cc[i] < fl[i] else 1
            else:
                trend[i] = 1 if cc[i] > fu[i] else -1
        st_level = fl[-1] if trend[-1] == 1 else fu[-1]
        add("supertrend", "Supertrend (10, 3)", _f(st_level), "bull" if trend[-1] == 1 else "bear",
            ("Uptrend; this level is the trailing support" if trend[-1] == 1
             else "Downtrend; this level is the trailing resistance"), "₹")
        out["atr"] = _f(atr.iloc[-1])

    # --- volatility
    if n >= 20:
        mid = c.rolling(20).mean()
        sd = c.rolling(20).std(ddof=0)          # population std, as charting platforms use
        upb, lob = float(mid.iloc[-1] + 2 * sd.iloc[-1]), float(mid.iloc[-1] - 2 * sd.iloc[-1])
        pb = (last - lob) / (upb - lob) if upb != lob else 0.5
        if pb > 1:
            rd, nt = "bear", "Closed above the upper band: stretched"
        elif pb < 0:
            rd, nt = "bull", "Closed below the lower band: stretched down"
        elif pb >= 0.5:
            rd, nt = "bull", "In the upper half of the bands"
        else:
            rd, nt = "bear", "In the lower half of the bands"
        add("bb", "Bollinger (20, 2) position", _f(100 * pb, 0), rd,
            f"{nt}. Bands ₹{lob:,.2f} to ₹{upb:,.2f}", "%")

    # --- volume
    if n >= 50:
        v20, v50 = float(v.tail(20).mean()), float(v.tail(50).mean())
        vr = float(v.iloc[-1]) / v20 if v20 > 0 else None
        if vr is not None:
            up_day = c.iloc[-1] >= c.iloc[-2]
            rd = "neutral" if vr < 1.5 else ("bull" if up_day else "bear")
            add("vol", "Volume vs 20-day average", _f(vr, 2), rd,
                (f"Heavy volume on an {'up' if up_day else 'down'} day" if vr >= 1.5 else "Ordinary volume")
                + f"; 20-day average is {v20 / v50:.2f}× the 50-day", "×")

    # --- range position and returns
    tail = c.tail(250)
    hi52, lo52 = float(tail.max()), float(tail.min())
    from_hi, from_lo = 100 * (last / hi52 - 1), 100 * (last / lo52 - 1)
    rd = "bull" if from_hi > -5 else "bear" if from_lo < 5 else "neutral"
    add("52w", "52-week closing range", None, rd,
        f"{abs(from_hi):.1f}% below the high ₹{hi52:,.2f}, {from_lo:.1f}% above the low ₹{lo52:,.2f}")
    out["returns"] = {lab: _f(100 * (last / float(c.iloc[-1 - k]) - 1), 1) if n > k else None
                      for lab, k in (("1W", 5), ("1M", 21), ("3M", 63), ("6M", 126), ("1Y", 250))}

    # --- classic pivot levels from the last session
    H, L, C = float(h.iloc[-1]), float(l.iloc[-1]), last
    P = (H + L + C) / 3
    out["pivots"] = {"S2": _f(P - (H - L)), "S1": _f(2 * P - H), "P": _f(P),
                     "R1": _f(2 * P - L), "R2": _f(P + (H - L))}

    counts = {"bull": 0, "bear": 0, "neutral": 0}
    for it in out["items"]:
        counts[it["reading"]] += 1
    out["summary"] = counts
    tot = counts["bull"] + counts["bear"]
    share = counts["bull"] / tot if tot else 0.5
    out["summary"]["label"] = ("Mostly bullish" if share >= 0.7 else "Leaning bullish" if share >= 0.55 else
                               "Mixed" if share > 0.45 else "Leaning bearish" if share > 0.3 else "Mostly bearish")
    out["as_of"] = df.index[-1].strftime("%Y-%m-%d")
    return out
