"""Geometric detectors for classic chart patterns.

Each detector looks at swing points (pivots) on closing prices and returns
`Candidate`s: the shape, the level whose close completes it, the measured-move
height and a 0..1 shape-quality score. Whether a candidate is still forming,
has broken out, or failed is decided later in `lifecycle.py`.

Bottoms are found by running the top logic on negated prices (`s = -1`), so
every rule is written once.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import Candidate, Line
from .pivots import Pivot


@dataclass
class Ctx:
    close: np.ndarray
    atrp: np.ndarray
    tf: str = "1D"

    @property
    def n(self) -> int:
        return len(self.close)

    # Timeframe-dependent sizes, in bars.
    @property
    def min_span(self) -> int:
        return {"1D": 12, "1W": 8, "1M": 6}[self.tf]

    @property
    def min_curve(self) -> int:
        return {"1D": 30, "1W": 15, "1M": 10}[self.tf]

    @property
    def max_curve(self) -> int:
        return {"1D": 400, "1W": 150, "1M": 80}[self.tf]

    def min_height(self, idx: int) -> float:
        """Smallest pattern height worth reporting, as a fraction of price."""
        return max(0.03, 2.0 * float(self.atrp[idx]))


def _clip(v: float) -> float:
    return float(min(1.0, max(0.0, v)))


def _seg(kind: str, x1, y1, x2, y2) -> dict:
    return {"kind": kind, "x1": int(x1), "y1": float(y1), "x2": int(x2), "y2": float(y2)}


def _poly(pivs: list[Pivot]) -> list[dict]:
    return [_seg("shape", a.idx, a.price, b.idx, b.price) for a, b in zip(pivs, pivs[1:])]


def _fit(points: list[Pivot]) -> Line:
    xs = np.array([p.idx for p in points], float)
    ys = np.array([p.price for p in points], float)
    if len(points) == 2:
        return Line.through(xs[0], ys[0], xs[1], ys[1])
    m, b = np.polyfit(xs, ys, 1)
    return Line(float(m), float(b))


def _formed(ctx: Ctx, last: Pivot, height: float, s: int) -> bool:
    """The final swing must have turned (not still running) before we call it a shape."""
    if last.confirmed:
        return True
    moved = s * (last.price - ctx.close[-1])
    return ctx.n - 1 - last.idx >= 2 and moved >= 0.3 * height


# ---------------------------------------------------------------- double / triple

def double_top_bottom(ctx: Ctx, piv: list[Pivot], scale: float) -> list[Candidate]:
    out = []
    for k in range(3, len(piv)):
        a, h1, v, h2 = piv[k - 3:k + 1]
        s = h2.kind  # +1 -> top, -1 -> bottom
        ref = (h1.price + h2.price) / 2
        H = s * (ref - v.price)
        if H <= 0 or H / ref < ctx.min_height(v.idx):
            continue
        if abs(h1.price - h2.price) > min(0.2 * H, 0.05 * ref):
            continue
        if s * (a.price - v.price) >= 0:      # needs a prior move into the pattern
            continue
        if h2.idx - h1.idx < max(6, ctx.min_span // 2):
            continue
        if not _formed(ctx, h2, H, s):
            continue
        eq = 1 - abs(h1.price - h2.price) / (0.2 * H)
        l1, l2 = v.idx - h1.idx, h2.idx - v.idx
        tsym = min(l1, l2) / max(l1, l2)
        size = _clip(H / ref / (2.5 * ctx.min_height(v.idx)))
        score = 0.45 * eq + 0.3 * tsym + 0.25 * size
        start = max(a.idx, h1.idx - (h2.idx - h1.idx) // 2)
        lab = "Top" if s == 1 else "Bottom"
        c = Candidate(
            pattern="Double Top" if s == 1 else "Double Bottom",
            bias="bear" if s == 1 else "bull",
            start=start, end=h2.idx, score=score, scale=scale,
            expiry=h2.idx + max(10, int(0.75 * (h2.idx - start))),
            points=[(h1.idx, h1.price, lab), (v.idx, v.price, "Neck"), (h2.idx, h2.price, lab)],
            segments=_poly([a, h1, v, h2])[1:],
        )
        _set_break(c, s, Line.flat(v.price), H, max(h1.price, h2.price) if s == 1 else min(h1.price, h2.price))
        out.append(c)
    return out


def triple_top_bottom(ctx: Ctx, piv: list[Pivot], scale: float) -> list[Candidate]:
    out = []
    for k in range(5, len(piv)):
        a, h1, v1, h2, v2, h3 = piv[k - 5:k + 1]
        s = h3.kind
        hs = np.array([h1.price, h2.price, h3.price])
        neck = min(v1.price, v2.price) if s == 1 else max(v1.price, v2.price)
        ref = hs.mean()
        H = s * (ref - neck)
        if H <= 0 or H / ref < ctx.min_height(v1.idx):
            continue
        if hs.max() - hs.min() > min(0.22 * H, 0.05 * ref):
            continue
        if s * (a.price - neck) >= 0:
            continue
        if h3.idx - h1.idx < ctx.min_span:
            continue
        if not _formed(ctx, h3, H, s):
            continue
        eq = 1 - (hs.max() - hs.min()) / (0.22 * H)
        g1, g2 = h2.idx - h1.idx, h3.idx - h2.idx
        tsym = min(g1, g2) / max(g1, g2)
        size = _clip(H / ref / (2.5 * ctx.min_height(v1.idx)))
        score = 0.45 * eq + 0.3 * tsym + 0.25 * size + 0.05
        lab = "Top" if s == 1 else "Bottom"
        start = max(a.idx, h1.idx - g1 // 2)
        c = Candidate(
            pattern="Triple Top" if s == 1 else "Triple Bottom",
            bias="bear" if s == 1 else "bull",
            start=start, end=h3.idx, score=min(score, 1.0), scale=scale,
            expiry=h3.idx + max(10, int(0.6 * (h3.idx - start))),
            points=[(h1.idx, h1.price, lab), (v1.idx, v1.price, ""), (h2.idx, h2.price, lab),
                    (v2.idx, v2.price, ""), (h3.idx, h3.price, lab)],
            segments=_poly([h1, v1, h2, v2, h3]),
        )
        _set_break(c, s, Line.flat(neck), H, hs.max() if s == 1 else hs.min())
        out.append(c)
    return out


def head_and_shoulders(ctx: Ctx, piv: list[Pivot], scale: float) -> list[Candidate]:
    out = []
    for k in range(5, len(piv)):
        a, ls, v1, hd, v2, rs = piv[k - 5:k + 1]
        s = rs.kind
        neck = Line.through(v1.idx, v1.price, v2.idx, v2.price)
        Hh = s * (hd.price - neck.at(hd.idx))
        if Hh <= 0 or Hh / hd.price < ctx.min_height(hd.idx):
            continue
        sh_l = s * (ls.price - neck.at(ls.idx))
        sh_r = s * (rs.price - neck.at(rs.idx))
        prom = s * hd.price - max(s * ls.price, s * rs.price)
        if min(sh_l, sh_r) < 0.35 * Hh or prom < 0.12 * Hh:
            continue
        if abs(ls.price - rs.price) > 0.35 * Hh or abs(v2.price - v1.price) > 0.45 * Hh:
            continue
        if s * (a.price - v1.price) >= 0:
            continue
        l, r = hd.idx - ls.idx, rs.idx - hd.idx
        tsym = min(l, r) / max(l, r)
        if tsym < 0.35 or rs.idx - ls.idx < ctx.min_span:
            continue
        if not _formed(ctx, rs, sh_r, s):
            continue
        sh_eq = 1 - abs(ls.price - rs.price) / (0.35 * Hh)
        flat = 1 - abs(v2.price - v1.price) / (0.45 * Hh)
        promi = _clip(prom / (0.4 * Hh))
        score = 0.3 * sh_eq + 0.25 * tsym + 0.2 * flat + 0.25 * promi
        start = max(a.idx, ls.idx - l // 2)
        names = ("Head & Shoulders", "Inverse Head & Shoulders")
        c = Candidate(
            pattern=names[0] if s == 1 else names[1],
            bias="bear" if s == 1 else "bull",
            start=start, end=rs.idx, score=score, scale=scale,
            expiry=rs.idx + max(10, int(0.6 * (rs.idx - start))),
            points=[(ls.idx, ls.price, "LS"), (v1.idx, v1.price, ""), (hd.idx, hd.price, "Head"),
                    (v2.idx, v2.price, ""), (rs.idx, rs.price, "RS")],
            segments=_poly([ls, v1, hd, v2, rs]),
        )
        _set_break(c, s, neck, Hh, rs.price)
        c.line_x0 = ls.idx
        out.append(c)
    return out


def _set_break(c: Candidate, s: int, line: Line, height: float, stop: float) -> None:
    """Single-sided reversal: s=+1 breaks down through `line`, s=-1 breaks up."""
    if s == 1:
        c.dn, c.height_dn, c.stop_dn = line, height, stop
        c.up = Line.flat(stop)            # close above the tops before breaking = invalid
    else:
        c.up, c.height_up, c.stop_up = line, height, stop
        c.dn = Line.flat(stop)


# ---------------------------------------------------------- trendline patterns

def trendline_patterns(ctx: Ctx, piv: list[Pivot], scale: float) -> list[Candidate]:
    """Triangles, wedges, rectangles, channels and broadening formations."""
    c = ctx.close
    out = []
    for k in range(3, len(piv)):
        for m in range(5, 9):          # at least 3 touches on one side, 2 on the other
            if k - m + 1 < 0:
                break
            w = piv[k - m + 1:k + 1]
            highs = [p for p in w if p.kind == 1]
            lows = [p for p in w if p.kind == -1]
            if len(highs) < 2 or len(lows) < 2:
                continue
            xs, xe = w[0].idx, w[-1].idx
            span = xe - xs
            if span < ctx.min_span:
                continue
            up, lo = _fit(highs), _fit(lows)
            w0, w1 = up.at(xs) - lo.at(xs), up.at(xe) - lo.at(xe)
            if w0 <= 0 or w1 <= 0:
                continue
            wavg = (w0 + w1) / 2
            ref = float(np.mean(c[xs:xe + 1]))
            if wavg / ref < 0.8 * ctx.min_height(xe):
                continue
            res = [abs(p.price - up.at(p.idx)) for p in highs] + [abs(p.price - lo.at(p.idx)) for p in lows]
            if max(res) > 0.18 * wavg:
                continue
            x = np.arange(xs, xe + 1)
            U, L = up.at(x), lo.at(x)
            W = U - L
            seg = c[xs:xe + 1]
            if (seg > U + 0.12 * W).any() or (seg < L - 0.12 * W).any():
                continue
            if not _formed(ctx, w[-1], 0.5 * w1, w[-1].kind):
                continue
            su = (up.at(xe) - up.at(xs)) / wavg
            sl = (lo.at(xe) - lo.at(xs)) / wavg
            r = w1 / w0
            fu, fl = abs(su) < 0.3, abs(sl) < 0.3
            name = bias = None
            clarity = 0.6
            if r < 0.72:
                if fu and sl >= 0.3:
                    name, bias, clarity = "Ascending Triangle", "bull", 1 - abs(su) / 0.3
                elif fl and su <= -0.3:
                    name, bias, clarity = "Descending Triangle", "bear", 1 - abs(sl) / 0.3
                elif su <= -0.3 and sl >= 0.3:
                    name, bias, clarity = "Symmetrical Triangle", "neutral", _clip(1 - abs(su + sl) / max(abs(su), abs(sl)))
                elif su >= 0.3 and sl > su:
                    name, bias = "Rising Wedge", "bear"
                elif sl <= -0.3 and su < sl:
                    name, bias = "Falling Wedge", "bull"
            elif r <= 1.38:
                if fu and fl:
                    name, bias, clarity = "Rectangle", "neutral", 1 - max(abs(su), abs(sl)) / 0.3
                elif su >= 0.3 and sl >= 0.3:
                    name, bias = "Ascending Channel", "neutral"
                elif su <= -0.3 and sl <= -0.3:
                    name, bias = "Descending Channel", "neutral"
            elif su >= 0.3 and sl <= -0.3:
                name, bias = "Broadening Formation", "neutral"
            if name is None:
                continue
            touch = 1 - float(np.mean(res)) / (0.18 * wavg)
            inside = float(np.mean((seg <= U + 0.02 * W) & (seg >= L - 0.02 * W)))
            touches = _clip((m - 5) / 3)
            score = 0.4 * touch + 0.2 * touches + 0.25 * inside + 0.15 * clarity
            expiry = xe + max(10, int(0.75 * span))
            if up.m != lo.m and r < 0.72:
                apex = (lo.b - up.b) / (up.m - lo.m)
                if apex > xe:
                    expiry = int(min(expiry, apex))
            last_hi = highs[-1].price
            last_lo = lows[-1].price
            # Measured move: triangles use the first swing (the widest part), wedges
            # retrace their whole range, ranges and channels use their average width.
            if "Triangle" in name:
                height = abs(highs[0].price - lows[0].price)
            elif "Wedge" in name:
                height = float(seg.max() - seg.min())
            else:
                height = wavg
            cand = Candidate(
                pattern=name, bias=bias, start=xs, end=xe, score=score, scale=scale,
                expiry=expiry, up=up, dn=lo,
                height_up=height, height_dn=height, stop_up=last_lo, stop_dn=last_hi,
                target_up=float(seg.max()) if name == "Falling Wedge" else None,
                target_dn=float(seg.min()) if name == "Rising Wedge" else None,
                up_x0=highs[0].idx, dn_x0=lows[0].idx, other_is_boundary=True,
                points=[(p.idx, p.price, "") for p in w],
                segments=_poly(w),
            )
            out.append(cand)
    return out


# ----------------------------------------------------------- flags & pennants

def _envelope(x: np.ndarray, y: np.ndarray, upper: bool, anchor0: bool) -> tuple[Line, int]:
    """Line through local extremes, shifted so it bounds every close.

    `anchor0` puts bar 0 (the pole tip) on this line: the upper line of a bull
    flag, the lower line of a bear flag. Returns the line and the number of
    distinct swing points touching it.
    """
    yy = y if upper else -y
    ext = [i for i in range(1, len(y) - 1) if yy[i] >= yy[i - 1] and yy[i] >= yy[i + 1]]
    idx = sorted(set(([0] if anchor0 else []) + ext + [len(y) - 1]))
    if len(idx) >= 2:
        m, b = np.polyfit(x[idx], yy[idx], 1)
    else:
        m, b = 0.0, float(yy.max())
    b += float(np.max(yy - (m * x + b)))
    tol = 0.15 * (np.ptp(y) + 1e-9)
    cand = ([0] if anchor0 else []) + ext
    touches = sum(1 for i in cand if abs(yy[i] - (m * x[i] + b)) <= tol)
    if upper:
        return Line(float(m), float(b)), touches
    return Line(float(-m), float(-b)), touches


def flags_pennants(ctx: Ctx, piv: list[Pivot], scale: float) -> list[Candidate]:
    c = ctx.close
    n = ctx.n
    out = []
    for k in range(1, len(piv)):
        b = piv[k]
        s = b.kind                       # +1: pole up (bull), -1: pole down (bear)
        # the pole starts at the most extreme close of the 15 bars before its tip
        lo = max(piv[k - 1].idx, b.idx - 15)
        w = s * c[lo:b.idx]
        if len(w) < 2:
            continue
        ai = lo + int(np.argmin(w))
        a = Pivot(ai, float(c[ai]), -s, True)
        pole = abs(b.price - a.price)
        gain = pole / a.price
        pbars = b.idx - a.idx
        ap = float(ctx.atrp[b.idx])
        if pbars < 2 or pbars > 15 or gain < max(0.08, 3 * ap) or gain / pbars < 0.6 * ap:
            continue
        if b.idx >= n - 4:
            continue
        max_len = min(30, max(8, 4 * pbars))

        def shape(e):
            """Flag/pennant geometry of bars b..e, or None if not a valid consolidation."""
            win = c[b.idx:e + 1]
            x = np.arange(b.idx, e + 1)
            U, tu = _envelope(x, win, True, s == 1)
            L, tl = _envelope(x, win, False, s == -1)
            w0, w1 = U.at(b.idx) - L.at(b.idx), U.at(e) - L.at(e)
            if len(win) < 5 or tu < 2 or tl < 2 or w0 <= 0 or w1 <= 0 or w0 > 0.45 * pole:
                return None
            apex = None
            if U.m != L.m:
                xa = (L.b - U.b) / (U.m - L.m)
                if xa > e:
                    apex = xa
            drift = s * ((U.at(e) + L.at(e)) / 2 - (U.at(b.idx) + L.at(b.idx)) / 2) / pole
            if drift > 0.12 or drift < -0.35:   # not with the pole, and not a steep V-rebound
                return None
            r = w1 / w0
            if r < 0.5:
                kind = "Pennant"
            elif r <= 1.5:
                kind = "Flag"
            else:
                return None
            return U, L, tu, tl, w0, kind, apex

        end = geo = None
        for t in range(b.idx + 4, min(n, b.idx + max_len + 1)):
            win = c[b.idx:t]
            if s * (b.price - (win.min() if s == 1 else win.max())) / pole > 0.5:
                break                    # retraced more than half the pole
            g = shape(t - 1)
            if g is not None:
                U, L, apex = g[0], g[1], g[6]
                if apex is not None and t >= apex:
                    break                # lines have met: the pennant is spent
                d = max(0.005, 0.35 * float(ctx.atrp[t]))
                lvl = U.at(t) if s == 1 else L.at(t)
                if s * (c[t] - lvl) / lvl >= d:      # decisive close out of the flag
                    end, geo = t - 1, g
                    break
            elif s * (c[t] - b.price) > 0:
                break                    # trend simply resumed without a flag
        else:
            last = n - 1
            if 4 <= last - b.idx <= max_len and s * (b.price - (c[b.idx:].min() if s == 1 else c[b.idx:].max())) / pole <= 0.5:
                g = shape(last)
                if g is not None:
                    end, geo = last, g
        if end is None:
            continue
        U, L, tu, tl, w0, kind, apex = geo
        win = c[b.idx:end + 1]
        name = ("Bull " if s == 1 else "Bear ") + kind
        retr = s * (b.price - (win.min() if s == 1 else win.max())) / pole
        steep = _clip((gain / pbars) / (1.5 * ap))
        score = 0.3 * steep + 0.3 * (1 - retr / 0.5) + 0.2 * (1 - w0 / (0.45 * pole)) + 0.2 * _clip((tu + tl - 3) / 4)
        cand = Candidate(
            pattern=name, bias="bull" if s == 1 else "bear",
            start=a.idx, end=end, score=score, scale=scale,
            expiry=b.idx + max_len if apex is None else min(b.idx + max_len, int(apex) - 1),
            points=[(a.idx, a.price, ""), (b.idx, b.price, "Pole")],
            segments=[_seg("pole", a.idx, a.price, b.idx, b.price)],
        )
        cand.line_x0 = b.idx
        # the opposite flag line doubles as the invalidation line
        cand.other_is_boundary = True
        if s == 1:
            cand.up, cand.height_up, cand.stop_up = U, pole, float(win.min())
            cand.dn = L
        else:
            cand.dn, cand.height_dn, cand.stop_dn = L, pole, float(win.max())
            cand.up = U
        out.append(cand)
    return out


# ------------------------------------------------------ cups & rounding curves

def _roundness(y: np.ndarray) -> tuple[float, float, float, float]:
    """Quadratic fit of a U shape: (r2, vertex position 0..1, U-ness, worst deviation / depth)."""
    xn = np.linspace(0, 1, len(y))
    a, b, c0 = np.polyfit(xn, y, 2)
    if a <= 0:
        return 0.0, 0.0, 0.0, 1.0
    fit = a * xn ** 2 + b * xn + c0
    ss = float(np.sum((y - y.mean()) ** 2)) or 1e-9
    r2 = 1 - float(np.sum((y - fit) ** 2)) / ss
    xv = -b / (2 * a)
    D = y.max() - y.min()
    u = float(np.mean(y <= y.min() + D / 3)) if D > 0 else 0
    mid = (xn >= 0.2) & (xn <= 0.8)          # a hump inside the U (a "W") is not a curve
    dev = float(np.max((y - fit)[mid]) / D) if D > 0 else 1.0
    return r2, float(xv), u, dev


def _is_w(y: np.ndarray) -> bool:
    """Two separate lows near the bottom with a real hump between them = a W, not a U."""
    lo, D = y.min(), y.max() - y.min()
    if D <= 0:
        return False
    deep = np.nonzero(y <= lo + 0.15 * D)[0]
    if len(deep) < 2 or deep[-1] - deep[0] < 0.2 * len(y):
        return False
    hump = y[deep[0]:deep[-1] + 1].max()
    return bool(hump - lo >= 0.3 * D)


def cups_and_curves(ctx: Ctx, pivs: list[Pivot], scale: float) -> list[Candidate]:
    c = ctx.close
    n = ctx.n
    out = []
    for p in pivs:
        s = p.kind                 # +1: left rim is a high -> bullish cup/rounding bottom
        yy = s * c                 # in this space the curve is always a "U" under the rim
        rr = s * p.price
        lo_i = p.idx + 1
        hi_i = min(n, p.idx + 1 + ctx.max_curve)
        if hi_i - lo_i < ctx.min_curve:
            continue
        seg = yy[lo_i:hi_i]
        above = np.nonzero(seg > rr)[0]
        if len(above):
            tb = lo_i + int(above[0])
            e = tb - 1
        else:
            if n - 1 >= p.idx + ctx.max_curve:
                continue
            tb, e = None, n - 1
        span = e - p.idx
        if span < ctx.min_curve:
            continue
        reg = yy[p.idx:e + 1]
        ib = int(np.argmin(reg))
        D = rr - reg[ib]
        depth = D / p.price
        if depth < max(0.10, 3 * float(ctx.atrp[p.idx])) or depth > (0.75 if s == 1 else 2.0):
            continue
        # right rim & handle
        handle = None
        near = np.nonzero(reg[ib:] >= rr - 0.2 * D)[0]
        if len(near):
            k0 = ib + int(near[0])                   # price first gets back near the rim
            hl = k0 + int(np.argmin(reg[k0:]))       # handle low
            j = k0 + int(np.argmax(reg[k0:hl + 1]))  # right rim, before the handle low
            hlen = span - j
            if hl > j and hlen >= 3 and j >= ctx.min_curve and hlen <= 0.5 * j:
                hdrop = (reg[j] - reg[hl]) / D
                if 0.03 <= hdrop <= 0.5:
                    handle = (j, hdrop)
        cup = reg[: (handle[0] + 1) if handle else span + 1]
        if handle is None and tb is None and (reg[-1] - reg[ib]) / D < 0.6:
            continue                           # still near the bottom: not a curve yet
        r2, xv, u, dev = _roundness(cup)
        if r2 < 0.65 or not (0.25 <= xv <= 0.75) or u < 0.18 or dev > 0.3 or _is_w(cup):
            continue
        score = 0.4 * _clip((r2 - 0.65) / 0.3) + 0.25 * _clip(1 - abs(xv - 0.5) / 0.25) + 0.2 * _clip(u / 0.4)
        if handle:
            sym = _clip(1 - (rr - reg[handle[0]]) / (0.2 * D))
            score += 0.15 * (0.5 * sym + 0.5 * _clip(1 - max(0, handle[1] - 0.33) / 0.17))
            name = "Cup & Handle" if s == 1 else "Inverted Cup & Handle"
        else:
            score += 0.15 * _clip(1 - abs(len(cup) / 2 - ib) / (len(cup) / 2))
            name = "Rounding Bottom" if s == 1 else "Rounding Top"
        bot_i = p.idx + ib
        price_D = D
        pts = [(p.idx, p.price, "L"), (bot_i, c[bot_i], "Bottom" if s == 1 else "Top")]
        segs = []
        # sample the fitted curve so the arc can be drawn
        xn = np.linspace(0, 1, len(cup))
        coef = np.polyfit(xn, cup, 2)
        xs_s = np.linspace(0, 1, 24)
        ys_s = s * np.polyval(coef, xs_s)
        xi = p.idx + xs_s * (len(cup) - 1)
        for i in range(len(xs_s) - 1):
            segs.append(_seg("curve", round(xi[i]), ys_s[i], round(xi[i + 1]), ys_s[i + 1]))
        cand = Candidate(
            pattern=name, bias="bull" if s == 1 else "bear",
            start=p.idx, end=e, score=min(score, 1.0), scale=scale,
            expiry=e + max(10, int(0.3 * span)),
            points=pts, segments=segs,
        )
        if handle:
            hj = p.idx + handle[0]
            pts.append((hj, c[hj], "R"))
            h_ext = c[hj:e + 1].min() if s == 1 else c[hj:e + 1].max()
            stop = float(h_ext)
        else:
            stop = p.price - s * 0.5 * price_D
        if s == 1:
            cand.up, cand.height_up, cand.stop_up = Line.flat(p.price), price_D, stop
            cand.dn = Line.flat(p.price - (0.5 if handle else 1.0) * price_D)
        else:
            cand.dn, cand.height_dn, cand.stop_dn = Line.flat(p.price), price_D, stop
            cand.up = Line.flat(p.price + (0.5 if handle else 1.0) * price_D)
        out.append(cand)
    return out
