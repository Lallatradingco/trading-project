"""Paths and settings. Everything can be overridden with environment variables."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("PATTERN_PORTAL_DATA", ROOT.parent / "pattern_data")).expanduser()
OHLC_DIR = DATA_DIR / "ohlc"          # one gzip CSV of daily candles per symbol
SCAN_DIR = DATA_DIR / "scan"          # latest scan results
META_DIR = DATA_DIR / "meta"          # universe lists, names, holidays

# Separate from Lalla Hub on purpose: its own port, localhost only by default.
HOST = os.environ.get("PATTERN_PORTAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PATTERN_PORTAL_PORT", "8765"))

# Free NSE end-of-day history (split/bonus adjusted), updated weekly, on GitHub.
EOD2_RAW = "https://raw.githubusercontent.com/BennyThadikaran/eod2_data/main"
EOD2_TREE = "https://api.github.com/repos/BennyThadikaran/eod2_data/git/trees/main?recursive=1"

MIN_HISTORY_BARS = int(os.environ.get("PATTERN_PORTAL_MIN_BARS", "250"))   # ~1 year of daily candles
MAX_STALE_DAYS = 20                   # skip symbols that stopped trading
TIMEFRAMES = ("1D", "1W", "1M")

BROWSER_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def load_settings() -> dict:
    import json
    p = META_DIR / "settings.json"
    s = {"universe": "all"}
    if p.exists():
        s.update(json.loads(p.read_text()))
    return s


def save_settings(**kw) -> None:
    import json
    ensure_dirs()
    s = load_settings()
    s.update(kw)
    (META_DIR / "settings.json").write_text(json.dumps(s, indent=1))


def ensure_dirs() -> None:
    for d in (OHLC_DIR, SCAN_DIR, META_DIR):
        d.mkdir(parents=True, exist_ok=True)
