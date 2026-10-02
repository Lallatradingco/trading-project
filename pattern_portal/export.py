"""Export the current scan as static files for a hosted, read-only copy of the portal.

    python -m pattern_portal export OUT_DIR

Writes OUT_DIR/data/*.json. The same static/app.js runs on them when the page
sets `window.PORTAL_STATIC = "data/"` (no Flask server needed).
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from pathlib import Path

import pandas as pd

from . import config, universe

LIGHT = ("id", "pattern", "family", "tf", "direction", "status", "outcome", "quality", "score",
         "start_date", "end_date", "breakout_date", "bars_ago", "breakout", "target", "stop", "rr",
         "range_up", "range_dn", "span", "volume_confirmed")
CHUNK_BYTES = 6_000_000


def _downsample(p: dict, points: int) -> dict:
    """Thin the chart but keep its first sample (the window start) and its last (latest close)."""
    ch = p["chart"]
    c = ch["c"]
    xs = ch.get("x") or [ch["i0"] + j * ch["step"] for j in range(len(c))]
    if len(c) <= points:
        return {**p, "chart": {**ch, "x": xs}}
    k = math.ceil(len(c) / points)
    pos = list(range(0, len(c), k))
    if pos[-1] != len(c) - 1:
        pos.append(len(c) - 1)
    return {**p, "chart": {**ch, "c": [c[j] for j in pos], "x": [xs[j] for j in pos],
                           "step": ch["step"] * k}}


def _dump(path: Path, obj) -> int:
    s = json.dumps(obj, separators=(",", ":"))
    path.write_text(s)
    return len(s)


def export(out: Path, recent: int = 120, chart_points: int = 100) -> dict:
    cur = config.SCAN_DIR / "current"
    data = out / "data"
    (data / "history").mkdir(parents=True, exist_ok=True)
    meta = json.loads((cur / "meta.json").read_text())
    stocks = json.loads((cur / "stocks.json").read_text())
    names = universe.load_names()
    lists = universe.load_lists()
    idx = pd.read_csv(cur / "index.csv.gz", usecols=["id", "bars_ago", "status"])
    keep = set(idx.loc[(idx.bars_ago <= recent) | idx.status.isin(["Forming", "Marginal"]), "id"])

    recent_pats, history = [], defaultdict(dict)
    for f in sorted((cur / "symbols").glob("*.json")):
        d = json.loads(f.read_text())
        sym = f.stem
        history[sym[0].upper() if sym[0].isalpha() else "_"][sym] = [
            {k: p.get(k) for k in LIGHT} for p in sorted(d["patterns"], key=lambda p: p["event_date"], reverse=True)]
        for p in d["patterns"]:
            if p["id"] in keep:
                q = _downsample(p, chart_points)
                q.pop("scale", None)
                recent_pats.append(q)

    chunks, buf, size = [], [], 0
    for p in recent_pats:
        n = len(json.dumps(p, separators=(",", ":")))
        if buf and size + n > CHUNK_BYTES:
            chunks.append(buf)
            buf, size = [], 0
        buf.append(p)
        size += n
    if buf:
        chunks.append(buf)
    for i, ch in enumerate(chunks):
        _dump(data / f"patterns_{i}.json", ch)
    for letter, rows in history.items():
        _dump(data / "history" / f"{letter}.json", rows)

    for s in stocks:
        s["name"] = names.get(s["symbol"], "")
    _dump(data / "stocks.json", stocks)
    for name in ("intraday.json", "reliability.json"):
        (data / name).write_text((cur / name).read_text())
    groups = {k: sorted(v) for k, v in lists.items()}
    for n in (100, 200, 500):
        groups[f"liq{n}"] = sorted(s["symbol"] for s in stocks if s.get("liq_rank", 10**9) <= n)
    _dump(data / "lists.json", groups)
    manifest = {**meta, "pattern_files": len(chunks), "history_keys": sorted(history),
                "recent_window": recent, "patterns_exported": len(recent_pats),
                "universes": [{"key": k, "label": universe.LABELS[k],
                               "available": k == "all" or bool(groups.get(k))} for k in universe.UNIVERSES]}
    _dump(data / "meta.json", manifest)
    build_pages(out)
    return manifest


def build_pages(out: Path) -> None:
    """index.html for any static host, artifact.html as a body-only fragment."""
    static = config.ROOT / "static"
    html = (static / "index.html").read_text()
    body = html.split("<!--PAGE-->", 1)[1].split("<!--/PAGE-->", 1)[0]
    css = (static / "app.css").read_text()
    js = (static / "app.js").read_text()
    fonts = ('<link rel="preconnect" href="https://fonts.googleapis.com">\n'
             '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
             '<link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600'
             '&family=IBM+Plex+Sans+Condensed:wght@500;600&display=swap" rel="stylesheet">')
    title = "<title>Lalla Pattern Scanner</title>"
    inline = (f"<style>\n{css}\n</style>\n{body}\n"
              f'<script>window.PORTAL_STATIC = "data/";</script>\n<script>\n{js}\n</script>\n')
    (out / "artifact.html").write_text(f"{title}\n{fonts}\n{inline}")
    (out / "index.html").write_text(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{title}\n{fonts}\n</head>\n<body>\n{inline}</body>\n</html>\n")
