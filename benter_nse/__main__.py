"""Command line: python -m benter_nse <command>

  universe   download the NSE 500 list
  fetch      pull / update daily candles from Kotak Neo (needs KOTAK_CONSUMER_KEY)
  run        walk-forward test + today's calls + report (the full job)
  signals    quick daily refresh of today's calls with the last trained model
  serve      open the portal (default http://127.0.0.1:8611)
  demo       run everything on synthetic data (no Kotak key needed)
"""

from __future__ import annotations

import argparse
import logging
import shutil
import sys
from datetime import date
from pathlib import Path

from . import config as config_mod

log = logging.getLogger("benter_nse")


def cmd_universe(cfg, args) -> int:
    from . import universe

    if universe.download(cfg.index_csv_url, cfg.index_csv):
        print(f"Saved {len(universe.load(cfg.index_csv))} stocks to {cfg.index_csv}")
        return 0
    print("NSE refused the download. Open the URL below in a browser and save the file to\n"
          f"  {cfg.index_csv}\n  {cfg.index_csv_url}")
    return 1


def cmd_fetch(cfg, args) -> int:
    from . import kotak, universe

    uni = universe.load(cfg.index_csv)
    if args.symbols:
        want = {s.strip().upper() for s in args.symbols.split(",")}
        uni = uni[uni["symbol"].isin(want)]
    client = kotak.make_client()
    tokens = kotak.match_tokens(uni, kotak.nse_tokens(client))
    if args.limit:
        tokens = tokens.head(args.limit)
    hist = kotak.History(client, cfg.prices_dir, pause_s=cfg.request_pause_s)
    years = args.years or cfg.years
    failed = []
    for i, row in enumerate(tokens.itertuples(index=False), 1):
        try:
            n = hist.update(row.symbol, int(row.token), years, today=date.today())
            log.info("[%d/%d] %s: %d days", i, len(tokens), row.symbol, n)
        except kotak.KotakAuthError:
            raise
        except Exception as exc:  # noqa: BLE001 - keep going, report at the end
            failed.append(row.symbol)
            log.warning("[%d/%d] %s failed: %s", i, len(tokens), row.symbol, exc)
    print(f"Done: {len(tokens) - len(failed)} stocks updated in {cfg.prices_dir}"
          + (f"; failed: {', '.join(failed)} (re-run fetch to retry)" if failed else ""))
    return 0


def cmd_run(cfg, args) -> int:
    from . import engine

    r = engine.run(cfg)
    bets = [s for s in r["signals"] if s["call"] == "BET"]
    print(f"\n{r['verdict']['label']}: {r['verdict']['text']}")
    print(f"Today ({r['as_of']}): {len(bets)} bets. Report: {cfg.out_dir / 'report.html'}")
    return 0


def cmd_signals(cfg, args) -> int:
    from . import engine

    r = engine.refresh_signals(cfg)
    bets = [s for s in r["signals"] if s["call"] == "BET"]
    print(f"Today ({r['as_of']}): {len(bets)} bets. Model verdict: {r['verdict']['label']}.")
    return 0


def cmd_serve(cfg, args) -> int:
    from . import portal

    print(f"Portal on http://{args.host}:{args.port or cfg.port}")
    portal.serve(cfg, host=args.host, port=args.port)
    return 0


def cmd_demo(cfg, args) -> int:
    from . import engine, synthetic

    demo_dir = config_mod.PKG_DIR / "data_demo"
    shutil.rmtree(demo_dir, ignore_errors=True)
    synthetic.make(demo_dir, signal=not args.no_signal)
    dcfg = config_mod.load(data_dir=demo_dir)
    r = engine.run(dcfg)
    print(f"\nSynthetic data ({'no edge planted' if args.no_signal else 'small edge planted'}):")
    print(f"{r['verdict']['label']}: {r['verdict']['text']}")
    print(f"Report: {dcfg.out_dir / 'report.html'}")
    if args.serve:
        from . import portal
        portal.serve(dcfg, host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m benter_nse", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", type=Path, help="path to config.toml")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("universe")
    f = sub.add_parser("fetch")
    f.add_argument("--symbols", help="comma-separated subset, e.g. ITC,MARUTI")
    f.add_argument("--limit", type=int, help="only the first N stocks (for a trial run)")
    f.add_argument("--years", type=int, help="override years of history")
    sub.add_parser("run")
    sub.add_parser("signals")
    for name in ("serve", "demo"):
        s = sub.add_parser(name)
        s.add_argument("--host", default="127.0.0.1")
        s.add_argument("--port", type=int)
        if name == "demo":
            s.add_argument("--no-signal", action="store_true", help="synthetic data with no edge")
            s.add_argument("--serve", action="store_true", help="open the portal afterwards")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    cfg = config_mod.load(args.config)
    handler = {"universe": cmd_universe, "fetch": cmd_fetch, "run": cmd_run,
               "signals": cmd_signals, "serve": cmd_serve, "demo": cmd_demo}[args.cmd]
    from .kotak import KotakAuthError

    try:
        return handler(cfg, args)
    except (FileNotFoundError, KotakAuthError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
