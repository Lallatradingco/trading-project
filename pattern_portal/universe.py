"""Index membership (Nifty 50/100/200/500, F&O) and company names.

Lists are downloaded from niftyindices.com / NSE archives and cached. If a
download fails the last cached copy is used; if there is none, that universe
chip is simply disabled in the portal.
"""

from __future__ import annotations

import io
import json
import logging

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

INDEX_URLS = {
    "nifty50": "https://www.niftyindices.com/IndexConstituent/ind_nifty50list.csv",
    "nifty100": "https://www.niftyindices.com/IndexConstituent/ind_nifty100list.csv",
    "nifty200": "https://www.niftyindices.com/IndexConstituent/ind_nifty200list.csv",
    "nifty500": "https://www.niftyindices.com/IndexConstituent/ind_nifty500list.csv",
}
FNO_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
EQUITY_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

UNIVERSES = ("nifty50", "nifty100", "nifty200", "fno", "nifty500",
             "liq100", "liq200", "liq500", "all")
LABELS = {"nifty50": "Nifty 50", "nifty100": "Nifty 100", "nifty200": "Nifty 200",
          "fno": "F&O", "nifty500": "Nifty 500", "liq100": "Top 100 by turnover",
          "liq200": "Top 200 by turnover", "liq500": "Top 500 by turnover", "all": "All stocks"}


def _get(session: requests.Session, url: str) -> str:
    r = session.get(url, timeout=30)
    r.raise_for_status()
    return r.text


def refresh(session: requests.Session) -> dict[str, int]:
    """Download index lists and company names; returns list sizes."""
    config.ensure_dirs()
    names: dict[str, str] = load_names()
    sizes = {}
    for key, url in INDEX_URLS.items():
        try:
            df = pd.read_csv(io.StringIO(_get(session, url)))
            syms = df["Symbol"].astype(str).str.strip().str.upper().tolist()
            (config.META_DIR / f"{key}.txt").write_text("\n".join(syms))
            for s, n in zip(syms, df.get("Company Name", [])):
                names.setdefault(s, str(n).strip())
            sizes[key] = len(syms)
        except Exception as e:  # noqa: BLE001
            log.warning("%s list unavailable: %s", key, e)
    try:
        df = pd.read_csv(io.StringIO(_get(session, FNO_URL)))
        df.columns = [c.strip().upper() for c in df.columns]
        syms = [s for s in df["SYMBOL"].astype(str).str.strip().str.upper()
                if s and s != "SYMBOL" and "NIFTY" not in s and s != "SENSEX"]
        (config.META_DIR / "fno.txt").write_text("\n".join(sorted(set(syms))))
        sizes["fno"] = len(set(syms))
    except Exception as e:  # noqa: BLE001
        log.warning("F&O list unavailable: %s", e)
    try:
        df = pd.read_csv(io.StringIO(_get(session, EQUITY_URL)))
        df.columns = [c.strip().upper() for c in df.columns]
        for s, n in zip(df["SYMBOL"], df["NAME OF COMPANY"]):
            names[str(s).strip().upper()] = str(n).strip()
    except Exception as e:  # noqa: BLE001
        log.warning("company names unavailable: %s", e)
    if names:
        (config.META_DIR / "names.json").write_text(json.dumps(names))
    return sizes


def load_lists() -> dict[str, set[str]]:
    out = {}
    for key in ("nifty50", "nifty100", "nifty200", "nifty500", "fno"):
        p = config.META_DIR / f"{key}.txt"
        if p.exists():
            out[key] = set(p.read_text().split())
    return out


def load_names() -> dict[str, str]:
    p = config.META_DIR / "names.json"
    return json.loads(p.read_text()) if p.exists() else {}
