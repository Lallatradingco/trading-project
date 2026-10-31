"""One self-contained HTML page from result.json — served by the portal, or opened directly."""

from __future__ import annotations

import html
from typing import Any


def _pct(x: Any, d: int = 1, signed: bool = False) -> str:
    if x is None:
        return "–"
    s = f"{x * 100:+.{d}f}%" if signed else f"{x * 100:.{d}f}%"
    return s


def _num(x: Any, d: int = 2) -> str:
    return "–" if x is None else f"{x:.{d}f}"


def _e(s: Any) -> str:
    return html.escape(str(s))


def _chart(curve: list[dict]) -> str:
    if len(curve) < 2:
        return "<p class='muted'>Not enough out-of-sample periods to chart.</p>"
    W, H, L, R, T, B = 640, 240, 44, 12, 14, 26
    ys = [p[k] for p in curve for k in ("eq_kelly", "eq_naive") if p.get(k) is not None] + [100]
    lo, hi = min(ys), max(ys)
    pad = (hi - lo) * 0.08 or 5
    lo, hi = lo - pad, hi + pad
    n = len(curve)

    def x(i): return L + (W - L - R) * i / (n - 1)
    def y(v): return T + (H - T - B) * (hi - v) / (hi - lo)

    def path(k):
        pts = [f"{x(i):.1f},{y(p[k]):.1f}" for i, p in enumerate(curve) if p.get(k) is not None]
        return "M" + " L".join(pts)

    ticks = []
    for i in range(5):
        v = lo + (hi - lo) * i / 4
        ticks.append(f"<line x1='{L}' x2='{W - R}' y1='{y(v):.1f}' y2='{y(v):.1f}' class='grid'/>"
                     f"<text x='{L - 6}' y='{y(v) + 4:.1f}' class='ax' text-anchor='end'>{v:.0f}</text>")
    anchor = {0: "start", n - 1: "end"}
    labels = "".join(
        f"<text x='{x(i):.1f}' y='{H - 6}' class='ax' text-anchor='{anchor.get(i, 'middle')}'>"
        f"{_e(curve[i]['date'][:7])}</text>"
        for i in sorted({0, n // 2, n - 1})
    )
    return f"""
<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Growth of 100 rupees">
  {''.join(ticks)}
  <line x1="{L}" x2="{W - R}" y1="{y(100):.1f}" y2="{y(100):.1f}" class="base"/>
  <path d="{path('eq_naive')}" class="ln naive"/>
  <path d="{path('eq_kelly')}" class="ln kelly"/>
  {labels}
</svg>
<div class="legend"><span><i class="sw kelly"></i>Engine (bar + fractional Kelly)</span>
<span><i class="sw naive"></i>Naive: model's top 10%, no crowd check</span></div>"""


def _signals(rows: list[dict]) -> str:
    bets = [r for r in rows if r.get("call") == "BET"]
    head = ("<tr><th>Stock</th><th class='hide-sm'>Sector</th><th class='r'>Crowd</th>"
            "<th class='r'>Engine</th><th class='r hide-sm'>Bar</th><th class='r'>Edge</th>"
            "<th class='r hide-sm'>Exp. net</th><th class='r'>Weight</th></tr>")

    def tr(r, cls=""):
        flag = " <span class='tag'>RSI div</span>" if r.get("bull_div") else ""
        return (f"<tr class='{cls}'><td class='wrap'><b>{_e(r['symbol'])}</b>{flag}"
                f"<div class='sub'>{_e(r['company'])}<span class='show-sm'> · {_e(r['industry'])}</span></div></td>"
                f"<td class='sub hide-sm'>{_e(r['industry'])}</td>"
                f"<td class='r'>{_pct(r['pi'])}</td><td class='r'><b>{_pct(r['c'])}</b></td>"
                f"<td class='r hide-sm'>{_pct(r['bar'])}</td>"
                f"<td class='r {'pos' if (r['edge'] or 0) > 0 else 'neg'}'>{_pct(r['edge'], 1, True)}</td>"
                f"<td class='r hide-sm'>{_pct(r['exp_net'], 2, True)}</td>"
                f"<td class='r'>{_pct(r['weight']) if r['weight'] else '–'}</td></tr>")

    if bets:
        top = f"<div class='scroll'><table>{head}{''.join(tr(r) for r in bets)}</table></div>"
        gross = sum(r["weight"] for r in bets)
        top += f"<p class='muted'>{len(bets)} bets, {_pct(gross, 0)} of capital in total.</p>"
    else:
        top = ("<p class='empty'>No stock clears the bar today. The engine's odds are not far "
               "enough above the crowd's to pay for costs — so it doesn't bet.</p>")
    near = [r for r in rows if r.get("call") != "BET"][:25]
    rest = (f"<details><summary>Closest to the bar ({len(near)} of {len(rows)} ranked)</summary>"
            f"<div class='scroll'><table>{head}{''.join(tr(r, 'dim') for r in near)}</table></div></details>")
    return top + rest


def _stats(bt: dict) -> str:
    k, n = bt.get("kelly") or {}, bt.get("naive") or {}
    rows = [
        ("Holding periods tested", lambda s: f"{s.get('periods', '–')}"),
        ("Years covered", lambda s: _num(s.get("years"), 1)),
        ("Average picks per period", lambda s: _num(s.get("avg_picks"), 1)),
        ("Picks that beat the average after costs", lambda s: _pct(s.get("hit_rate"))),
        ("Average return per period (vs avg stock)", lambda s: _pct(s.get("avg_period_ret"), 2, True)),
        ("Periods with a gain", lambda s: _pct(s.get("pct_positive"), 0)),
        ("Worst period", lambda s: _pct(s.get("worst_period"), 1, True)),
        ("₹100 became", lambda s: "–" if s.get("growth_of_100") is None else f"₹{s['growth_of_100']:.0f}"),
        ("t-stat (2+ = unlikely to be luck)", lambda s: _num(s.get("t_stat"), 1)),
    ]
    body = "".join(f"<tr><td class='wrap'>{a}</td><td class='r'>{f(k)}</td><td class='r'>{f(n)}</td></tr>" for a, f in rows)
    return (f"<div class='scroll'><table><tr><th></th><th class='r'>Engine</th><th class='r'>Naive top 10%</th></tr>"
            f"{body}</table></div>")


def _years(yearly: list[dict]) -> str:
    def auc(v): return "–" if v is None else f"{v * 100:.1f}%"
    body = "".join(
        f"<tr><td>{y['year']}</td><td class='r hide-sm'>{y['rows']:,}</td><td class='r'>{auc(y.get('auc_pi'))}</td>"
        f"<td class='r'>{auc(y.get('auc_f'))}</td><td class='r'>{auc(y.get('auc_c'))}</td>"
        f"<td class='r'>{_num(y.get('alpha_model'))}</td></tr>" for y in yearly)
    return ("<div class='scroll'><table><tr><th>Year</th><th class='r hide-sm'>Stock-weeks</th><th class='r'>Crowd</th>"
            "<th class='r'>Model</th><th class='r'>Combined</th><th class='r'>Model weight</th></tr>"
            f"{body}</table></div>")


def _coefs(coefs: list[dict]) -> str:
    if not coefs:
        return ""
    mx = max(abs(c["weight"]) for c in coefs) or 1
    out = []
    for c in coefs:
        w = c["weight"]
        pct = abs(w) / mx * 50
        side = "pos" if w > 0 else "neg"
        bar = (f"<span class='cb {side}' style='width:{pct:.1f}%;{'left:50%' if w > 0 else f'left:{50 - pct:.1f}%'}'></span>")
        out.append(f"<div class='crow'><div class='cl'>{_e(c['label'])}</div><div class='ct'>{bar}<span class='mid'></span></div>"
                   f"<div class='cv'>{w:+.3f}</div></div>")
    return "<div class='coefs'>" + "".join(out) + "</div>"


def render(r: dict) -> str:
    v = r["verdict"]
    s = r["summary"]
    st = r["settings"]
    cls = {"EDGE": "ok", "WEAK EDGE": "warn"}.get(v["label"], "bad")
    ca = r.get("corporate_actions") or []
    adj = [c for c in ca if c["kind"] == "adjusted"]
    gaps = [c for c in ca if c["kind"] != "adjusted"]
    ca_html = ""
    if ca:
        items = "".join(f"<li>{_e(c['symbol'])} on {_e(c['date'])}: ×{c['factor']} ({_e(c['kind'])})</li>" for c in ca[:40])
        ca_html = (f"<details><summary>Price-history checks: {len(adj)} split/bonus adjusted, "
                   f"{len(gaps)} large gaps left as is</summary><ul class='sub'>{items}</ul></details>")
    auc_c = s.get("auc_c")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>NSE Benter Engine</title>
<style>
:root{{--bg:#f7f6f2;--card:#fff;--ink:#1d1d1b;--muted:#6b6a65;--line:#e4e2db;--pos:#1f7a4d;--neg:#b03a2e;
--warn:#a8650b;--kelly:#2747a8;--naive:#a39e93;--accent:#2747a8;}}
@media (prefers-color-scheme:dark){{:root{{--bg:#141413;--card:#1d1d1b;--ink:#ecebe6;--muted:#9c9a93;--line:#33322e;
--pos:#5cc08d;--neg:#e2766a;--warn:#e0a24a;--kelly:#8ea6ff;--naive:#77736a;--accent:#8ea6ff;}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:20px 16px 60px}}
h1{{font-size:22px;margin:0}}h2{{font-size:16px;margin:0 0 10px}}
.top{{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:14px}}
.muted,.sub{{color:var(--muted);font-size:13px}}.sub{{font-weight:400}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;margin:14px 0}}
.verdict{{border-left:6px solid var(--neg)}}.verdict.ok{{border-left-color:var(--pos)}}.verdict.warn{{border-left-color:var(--warn)}}
.badge{{font-weight:700;letter-spacing:.04em;font-size:13px;color:var(--neg)}}.ok .badge{{color:var(--pos)}}.warn .badge{{color:var(--warn)}}
.verdict p{{margin:6px 0 0;font-size:16px}}
.kpis{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-top:12px}}
.kpi{{border:1px solid var(--line);border-radius:10px;padding:10px}}.kpi b{{display:block;font-size:20px}}
.scroll{{overflow-x:auto}}table{{border-collapse:collapse;width:100%;font-size:14px}}
th,td{{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top;white-space:nowrap}}
th{{font-size:12px;color:var(--muted);font-weight:600}}.r{{text-align:right}}
.pos{{color:var(--pos)}}.neg{{color:var(--neg)}}tr.dim td{{opacity:.75}}
.tag{{font-size:11px;border:1px solid var(--accent);color:var(--accent);border-radius:6px;padding:0 4px;margin-left:4px}}
.empty{{font-size:16px;padding:6px 0}}
details{{margin-top:10px}}summary{{cursor:pointer;color:var(--accent);font-size:14px}}
.chart{{width:100%;height:auto}}.grid{{stroke:var(--line)}}.base{{stroke:var(--muted);stroke-dasharray:3 3}}
.ax{{fill:var(--muted);font-size:11px}}.ln{{fill:none;stroke-width:2}}.ln.kelly{{stroke:var(--kelly)}}.ln.naive{{stroke:var(--naive)}}
.legend{{display:flex;gap:16px;flex-wrap:wrap;font-size:13px;color:var(--muted)}}
.sw{{display:inline-block;width:14px;height:3px;vertical-align:middle;margin-right:6px}}.sw.kelly{{background:var(--kelly)}}.sw.naive{{background:var(--naive)}}
.crow{{display:grid;grid-template-columns:minmax(150px,1.3fr) 2fr 56px;gap:8px;align-items:center;font-size:13px;padding:3px 0}}
.ct{{position:relative;height:12px}}.cb{{position:absolute;top:0;height:12px;border-radius:3px}}.cb.pos{{background:var(--pos)}}.cb.neg{{background:var(--neg)}}
.mid{{position:absolute;left:50%;top:-2px;bottom:-2px;border-left:1px solid var(--muted)}}.cv{{text-align:right;color:var(--muted)}}
ul{{padding-left:18px}}.show-sm{{display:none}}
@media (max-width:600px){{.hide-sm{{display:none}}.show-sm{{display:inline}}td.wrap{{white-space:normal}}
th,td{{padding:7px 5px}}.crow{{grid-template-columns:minmax(110px,1fr) 1.2fr 50px}}}}
</style></head><body><main>
<div class="top"><h1>NSE 500 · Benter engine</h1>
<span class="muted">Prices to {_e(r['as_of'])} · built {_e(r['generated'])}</span></div>

<section class="verdict {cls}"><div class="badge">{_e(v['label'])}</div><p>{_e(v['text'])}</p>
<div class="kpis">
<div class="kpi"><span class="muted">Crowd alone</span><b>{_pct(s.get('auc_pi'))}</b><span class="muted">ranks the better of two stocks</span></div>
<div class="kpi"><span class="muted">Model alone</span><b>{_pct(s.get('auc_f'))}</b><span class="muted">ranks the better of two stocks</span></div>
<div class="kpi"><span class="muted">Combined</span><b>{_pct(auc_c)}</b><span class="muted">ranks the better of two stocks</span></div>
<div class="kpi"><span class="muted">Weight earned by model</span><b>{_num(s.get('alpha_model'))}</b><span class="muted">0 = adds nothing</span></div>
</div>
<p class="muted">50% is a coin flip. Measured only on years the models never saw ({', '.join(str(y) for y in s.get('honest_years', []))}).
Benter's skill score, pseudo-R²: crowd {_num(s.get('r2_pi'), 4)}, model {_num(s.get('r2_f'), 4)}, combined {_num(s.get('r2_c'), 4)}.</p>
</section>

<section><h2>Today's calls · next {st['horizon_days']} trading days</h2>
<p class="muted">A bet needs the engine's odds of beating the median NSE 500 stock to clear the crowd's
fair-price odds by enough to pay {st['round_trip_bps']:.0f} bps of round-trip costs. Weight = {st['kelly_fraction']:g}× Kelly,
capped at {_pct(st['max_weight'], 0)} per stock. {r['universe']['eligible_today']} stocks passed the ₹{st['min_turnover_cr']:g} cr liquidity filter.</p>
{_signals(r['signals'])}
</section>

<section><h2>Out-of-sample test, after costs</h2>
{_chart(r['backtest'].get('curve', []))}
{_stats(r['backtest'])}
<p class="muted">Returns are against the average NSE 500 stock, as if hedged with an equal-weight basket.
Each period holds {st['horizon_days']} trading days; periods do not overlap.</p>
</section>

<section><h2>Year by year (each year scored by models fitted only on earlier years)</h2>
{_years(r['yearly'])}
</section>

<section><h2>What the model leans on</h2>
<p class="muted">Green pushes a stock's odds up, red pushes them down. Factors are ranked against all stocks on the same day.</p>
{_coefs(r['coefficients'])}
</section>

<section><h2>Read before trading</h2><ul class="muted">
<li>The NSE 500 list is today's, so stocks that dropped out in the past are missing (survivorship bias). Past results look better than reality.</li>
<li>Costs are assumed at {st['round_trip_bps']:.0f} bps a round trip; small caps cost more.</li>
<li>Kotak serves 180 days per call; candles are checked for splits and bonuses (below).</li>
<li>Signals only: nothing here places orders. Not investment advice.</li></ul>
{ca_html}
</section>
</main></body></html>"""
