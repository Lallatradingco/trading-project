"""Run the pattern engine over every eligible stock and save the results.

Output (in pattern_data/scan/current/):
  meta.json          - run summary shown in the portal header
  index.csv.gz       - one light row per pattern, for fast filtering
  symbols/<SYM>.json - full records (lines, labels, chart closes) per stock
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Callable, Iterable, Optional

import pandas as pd

from . import config, data
from .engine import analyze

log = logging.getLogger(__name__)

INDEX_COLS = ["id", "symbol", "tf", "pattern", "family", "direction", "status", "quality",
              "score", "breakout", "target", "stop", "rr", "range_up", "range_dn", "bars_ago",
              "start_date", "end_date", "event_date", "breakout_date", "span", "volume_confirmed",
              "outcome", "last_close", "vs_breakout"]


def _scan_one(sym: str, timeframes: tuple[str, ...]) -> dict:
    df = data.load(sym)
    if df is None or df.empty:
        return {"symbol": sym, "ok": False, "why": "no data"}
    df = df.dropna(subset=["Close"])
    df = df[df["Close"] > 0]
    out = []
    last_close = float(df["Close"].iloc[-1])
    for tf in timeframes:
        d = data.resample(df, tf)
        try:
            recs = analyze(d, tf)
        except Exception as e:  # noqa: BLE001
            log.exception("%s %s failed", sym, tf)
            return {"symbol": sym, "ok": False, "why": str(e)}
        for r in recs:
            r["symbol"] = sym
            r["id"] = f"{sym}|{tf}|{r['pattern']}|{r['start_date']}|{r['end_date']}"
            r["last_close"] = round(last_close, 2)
            b = r["breakout"]
            r["vs_breakout"] = None if not b else round((last_close / b - 1) * 100, 1)
        out += recs
    info = {
        "symbol": sym,
        "last_close": round(last_close, 2),
        "prev_close": round(float(df["Close"].iloc[-2]), 2) if len(df) > 1 else None,
        "last_date": df.index[-1].strftime("%Y-%m-%d"),
        "first_date": df.index[0].strftime("%Y-%m-%d"),
        "bars": int(len(df)),
        "years": round((df.index[-1] - df.index[0]).days / 365.25, 1),
    }
    return {"symbol": sym, "ok": True, "info": info, "patterns": out}


def eligible(symbols: Iterable[str]) -> tuple[list[str], dict[str, str]]:
    """Enough history and still trading. Returns (eligible, skipped -> reason)."""
    ok, skipped, last = [], {}, {}
    for s in symbols:
        df = data.load(s)
        if df is None or len(df) < config.MIN_HISTORY_BARS:
            skipped[s] = "history"
            continue
        last[s] = df.index[-1]
    if last:
        newest = max(last.values())
        for s, d in last.items():
            if (newest - d).days > config.MAX_STALE_DAYS:
                skipped[s] = "stale"
            else:
                ok.append(s)
    return sorted(ok), skipped


def run(symbols: Optional[Iterable[str]] = None, timeframes=config.TIMEFRAMES,
        workers: Optional[int] = None,
        progress: Optional[Callable[[int, int], None]] = None) -> dict:
    config.ensure_dirs()
    t0 = time.time()
    listed = list(symbols) if symbols is not None else data.cached_symbols()
    todo, skipped = eligible(listed)
    nxt = config.SCAN_DIR / "next"
    shutil.rmtree(nxt, ignore_errors=True)
    (nxt / "symbols").mkdir(parents=True)
    rows, failed, with_patterns, newest = [], [], 0, None
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(workers) as ex:
        futs = [ex.submit(_scan_one, s, tuple(timeframes)) for s in todo]
        for i, f in enumerate(as_completed(futs), 1):
            res = f.result()
            if not res["ok"]:
                failed.append(res["symbol"])
            else:
                pats = res["patterns"]
                with_patterns += bool(pats)
                (nxt / "symbols" / f"{res['symbol']}.json").write_text(
                    json.dumps({"info": res["info"], "patterns": pats}, separators=(",", ":")))
                rows += [{k: p.get(k) for k in INDEX_COLS} for p in pats]
                ld = res["info"]["last_date"]
                newest = ld if newest is None or ld > newest else newest
            if progress:
                progress(i, len(todo))
    idx = pd.DataFrame(rows, columns=INDEX_COLS)
    idx.to_csv(nxt / "index.csv.gz", index=False, compression="gzip")
    meta = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1),
        "listed": len(listed),
        "eligible": len(todo),
        "scanned": len(todo) - len(failed),
        "failed": len(failed),
        "skipped": len(skipped),
        "skipped_history": sum(1 for v in skipped.values() if v == "history"),
        "with_patterns": with_patterns,
        "patterns": int(len(idx)),
        "data_through": newest,
        "timeframes": list(timeframes),
        "min_history_bars": config.MIN_HISTORY_BARS,
    }
    (nxt / "meta.json").write_text(json.dumps(meta, indent=1))
    cur, old = config.SCAN_DIR / "current", config.SCAN_DIR / "old"
    shutil.rmtree(old, ignore_errors=True)
    if cur.exists():
        cur.rename(old)
    nxt.rename(cur)
    shutil.rmtree(old, ignore_errors=True)
    return meta
