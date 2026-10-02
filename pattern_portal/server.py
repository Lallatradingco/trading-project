"""Standalone web portal: its own Flask app, its own port. Not part of Lalla Hub."""

from __future__ import annotations

import json
import logging
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

import requests
from flask import Flask, jsonify, request, send_from_directory

from . import config, data, scanner, universe
from .store import Store

log = logging.getLogger(__name__)
IST = timezone(timedelta(hours=5, minutes=30))
STATIC = config.ROOT / "static"


class Job:
    """One background refresh at a time: sync data, then scan."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self.lock = threading.Lock()
        self.state = {"running": False, "stage": "idle", "done": 0, "total": 0,
                      "started": None, "error": None, "last": None}

    def start(self, universe_key: str = "all", do_sync: bool = True) -> bool:
        with self.lock:
            if self.state["running"]:
                return False
            self.state.update(running=True, stage="starting", done=0, total=0,
                              started=time.time(), error=None)
        threading.Thread(target=self._run, args=(universe_key, do_sync), daemon=True).start()
        return True

    def _prog(self, stage):
        def f(i, n):
            self.state.update(stage=stage, done=i, total=n)
        return f

    def _run(self, universe_key: str, do_sync: bool) -> None:
        try:
            syms = symbols_for(universe_key, refresh_lists=do_sync)
            if do_sync:
                self.state["stage"] = "downloading"
                data.sync(syms, progress=self._prog("downloading"))
                data.topup_yahoo([s for s in syms if data.load(s) is not None], progress=self._prog("top-up"))
            meta = scanner.run([s for s in syms if (config.OHLC_DIR / f"{s}.csv.gz").exists()],
                               progress=self._prog("scanning"))
            self.store.reload()
            self.state["last"] = meta
        except Exception as e:  # noqa: BLE001
            log.error("refresh failed: %s", traceback.format_exc())
            self.state["error"] = str(e)
        finally:
            self.state.update(running=False, stage="idle")


def symbols_for(universe_key: str, refresh_lists: bool = True) -> list[str]:
    """Symbols to download/scan for a universe (falls back to the local cache)."""
    with requests.Session() as s:
        s.headers.update(config.BROWSER_HEADERS)
        if refresh_lists:
            universe.refresh(s)
            data.refresh_holidays(s)
        lists = universe.load_lists()
        if universe_key != "all" and universe_key in lists:
            return sorted(lists[universe_key])
        if refresh_lists:
            syms = data.eod2_symbols(s)
            if syms:
                return syms
    return data.cached_symbols()


def market_status() -> dict:
    now = datetime.now(IST)
    hol_p = config.META_DIR / "holidays.json"
    holidays = set(json.loads(hol_p.read_text())) if hol_p.exists() else set()

    def trading(d):
        return d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays

    open_t = now.replace(hour=9, minute=15, second=0, microsecond=0)
    close_t = now.replace(hour=15, minute=30, second=0, microsecond=0)
    is_open = trading(now) and open_t <= now < close_t
    if is_open:
        mins = int((close_t - now).total_seconds() // 60)
        note = f"Closes in {mins // 60}h {mins % 60}m"
    else:
        nxt = open_t if (trading(now) and now < open_t) else None
        d = now
        while nxt is None:
            d = d + timedelta(days=1)
            if trading(d):
                nxt = d.replace(hour=9, minute=15, second=0, microsecond=0)
        mins = int((nxt - now).total_seconds() // 60)
        note = f"Opens in {mins // 60}h {mins % 60}m" if mins < 24 * 60 else f"Opens {nxt:%a %d %b}, 9:15"
    return {"open": is_open, "time": now.strftime("%H:%M IST"), "note": note}


def create_app() -> Flask:
    config.ensure_dirs()
    app = Flask(__name__, static_folder=None)
    store = Store()
    job = Job(store)
    app.config["store"], app.config["job"] = store, job

    @app.get("/")
    def index():
        return send_from_directory(STATIC, "index.html")

    @app.get("/static/<path:p>")
    def static_files(p):
        return send_from_directory(STATIC, p)

    @app.get("/api/meta")
    def meta():
        lists = store.lists
        return jsonify({
            "scan": store.meta,
            "market": market_status(),
            "job": job.state,
            "scan_universe": config.load_settings()["universe"],
            "universes": [{"key": k, "label": universe.LABELS[k],
                           "available": k == "all" or k in lists,
                           "size": len(lists.get(k, ())) if k != "all" else None}
                          for k in universe.UNIVERSES],
        })

    @app.get("/api/patterns")
    def patterns():
        a = request.args
        f = {k: a.get(k) for k in ("universe", "q", "symbol", "within", "quality", "sort",
                                   "page", "per_page")}
        f = {k: v for k, v in f.items() if v not in (None, "")}
        for k in ("family", "direction", "tf", "status", "pattern"):
            vals = [v for v in a.getlist(k) if v]
            if vals:
                f[k] = vals
        f["volume"] = a.get("volume") == "1"
        f["from_book"] = a.get("from_book") == "1"
        return jsonify(store.query(f))

    @app.get("/api/stock/<sym>")
    def stock(sym):
        d = store.stock(sym)
        if d is None:
            return jsonify({"error": "not found"}), 404
        return jsonify(d)

    @app.post("/api/scan")
    def scan():
        body = request.get_json(silent=True) or {}
        ok = job.start(config.load_settings()["universe"], bool(body.get("sync", True)))
        return jsonify({"started": ok, "job": job.state}), (202 if ok else 409)

    @app.get("/api/scan/status")
    def scan_status():
        return jsonify(job.state)

    return app
