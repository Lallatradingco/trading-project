"""NSE 500 constituents (symbol, company, industry, ISIN)."""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def download(url: str, dest: Path) -> bool:
    """Try to fetch the NSE 500 list. Returns False if NSE refuses."""
    import requests

    try:
        r = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
        r.raise_for_status()
        df = _parse(r.content)
    except Exception as exc:  # noqa: BLE001 - any failure means "save it by hand"
        log.warning("Could not download the NSE 500 list (%s).", exc)
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    log.info("Saved %d NSE 500 constituents to %s", len(df), dest)
    return True


def _parse(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw))
    df.columns = [c.strip() for c in df.columns]
    need = {"Symbol", "Industry"}
    if not need.issubset(df.columns):
        raise ValueError(f"unexpected columns {list(df.columns)}")
    return df


def load(path: Path) -> pd.DataFrame:
    """Return columns: symbol, company, industry, isin."""
    if not path.exists():
        raise FileNotFoundError(
            f"NSE 500 list not found at {path}. Run `python -m benter_nse universe`, "
            "or download ind_nifty500list.csv from niftyindices.com and save it there."
        )
    df = _parse(path.read_bytes())
    out = pd.DataFrame(
        {
            "symbol": df["Symbol"].astype(str).str.strip().str.upper(),
            "company": df.get("Company Name", df["Symbol"]).astype(str).str.strip(),
            "industry": df["Industry"].astype(str).str.strip(),
            "isin": df.get("ISIN Code", pd.Series([""] * len(df))).astype(str).str.strip(),
        }
    )
    return out.drop_duplicates("symbol").reset_index(drop=True)
