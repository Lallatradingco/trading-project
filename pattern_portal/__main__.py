"""Command line for the standalone pattern portal.

    python -m pattern_portal setup --universe nifty500   # first download + scan
    python -m pattern_portal refresh                     # update data + rescan
    python -m pattern_portal serve                       # open http://127.0.0.1:8765
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from . import config, data, scanner
from .server import create_app, symbols_for
from .universe import UNIVERSES


def _bar(label):
    t0 = time.time()

    def f(i, n):
        if i == n or i % 25 == 0:
            pct = 100 * i / max(n, 1)
            sys.stderr.write(f"\r{label}: {i}/{n} ({pct:.0f}%)  {time.time() - t0:.0f}s ")
            if i == n:
                sys.stderr.write("\n")
    return f


def refresh(universe_key: str, sync: bool = True, topup: bool = True) -> dict:
    syms = symbols_for(universe_key, refresh_lists=sync)
    print(f"Universe '{universe_key}': {len(syms)} symbols")
    if sync:
        stats = data.sync(syms, progress=_bar("Downloading"))
        print("Data:", stats)
        if topup:
            n = data.topup_yahoo([s for s in syms if data.load(s) is not None], progress=_bar("Yahoo top-up"))
            print(f"Topped up {n} symbols with the latest sessions")
    have = [s for s in syms if (config.OHLC_DIR / f"{s}.csv.gz").exists()]
    meta = scanner.run(have, progress=_bar("Scanning"))
    print(f"Scanned {meta['scanned']} of {meta['eligible']} eligible stocks in {meta['seconds']}s: "
          f"{meta['patterns']} patterns, data through {meta['data_through']} "
          f"({meta['skipped']} skipped for short/stale history)")
    return meta


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(prog="pattern_portal", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("setup", help="choose the scan universe, download history and scan")
    s.add_argument("--universe", choices=[u for u in UNIVERSES if not u.startswith("liq")], default="all")
    r = sub.add_parser("refresh", help="update data and rescan the saved universe")
    r.add_argument("--no-topup", action="store_true", help="skip the Yahoo top-up of recent days")
    sub.add_parser("scan", help="rescan cached data only (no downloads)")
    e = sub.add_parser("export", help="write a static, read-only copy of the latest scan")
    e.add_argument("out")
    v = sub.add_parser("serve", help="run the portal")
    v.add_argument("--host", default=config.HOST)
    v.add_argument("--port", type=int, default=config.PORT)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    config.ensure_dirs()

    if a.cmd == "setup":
        config.save_settings(universe=a.universe)
        refresh(a.universe)
    elif a.cmd == "refresh":
        refresh(config.load_settings()["universe"], topup=not a.no_topup)
    elif a.cmd == "scan":
        refresh(config.load_settings()["universe"], sync=False)
    elif a.cmd == "export":
        from pathlib import Path
        from .export import export
        m = export(Path(a.out))
        print(f"Exported {m['patterns_exported']} recent patterns in {m['pattern_files']} files to {a.out}/data")
    elif a.cmd == "serve":
        print(f"Pattern portal on http://{a.host}:{a.port}  (Ctrl+C to stop)")
        create_app().run(host=a.host, port=a.port, debug=False, threaded=True)


if __name__ == "__main__":
    main()
