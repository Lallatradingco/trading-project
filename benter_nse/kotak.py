"""Daily price history from the Kotak Neo Trade API.

Only the consumer key from the NEO app (More -> Trade API) is needed: the
scrip master and historical candles do not require a TOTP/MPIN login, and
nothing here can place an order.

Kotak serves at most 180 days of daily candles per call, so each stock is
fetched in 180-day windows, walking back in time until the requested number
of years is covered or the stock's listing date is reached.
"""

from __future__ import annotations

import io
import logging
import os
import time
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Callable

import pandas as pd

log = logging.getLogger(__name__)

WINDOW_DAYS = 179  # from_date..to_date spans 180 calendar days inclusive
PRICE_COLS = ["date", "open", "high", "low", "close", "volume"]


class KotakAuthError(RuntimeError):
    pass


def make_client(consumer_key: str | None = None):
    """Create the SDK client from the KOTAK_CONSUMER_KEY environment variable."""
    key = consumer_key or os.environ.get("KOTAK_CONSUMER_KEY")
    if not key:
        raise KotakAuthError(
            "Set KOTAK_CONSUMER_KEY to the token from NEO app -> More -> Trade API."
        )
    from neo_api_client import NeoAPI  # pip install kotakneoapi

    return NeoAPI(consumer_key=key, environment="prod")


def _http_get(url: str) -> bytes:
    import httpx

    r = httpx.get(url, timeout=60, follow_redirects=True)
    r.raise_for_status()
    return r.content


# --------------------------------------------------------------------------- tokens

def nse_tokens(client: Any, http_get: Callable[[str], bytes] = _http_get) -> pd.DataFrame:
    """NSE cash-market scrip master: symbol, trd_symbol, token, isin, group."""
    url = client.scrip_master(exchange_segment="nse_cm")
    if not isinstance(url, str):
        raise RuntimeError(f"Scrip master lookup failed: {url}")
    df = pd.read_csv(io.BytesIO(http_get(url)), low_memory=False)
    df.columns = [c.strip() for c in df.columns]
    out = pd.DataFrame(
        {
            "symbol": df["pSymbolName"].astype(str).str.strip().str.upper(),
            "trd_symbol": df["pTrdSymbol"].astype(str).str.strip(),
            "token": pd.to_numeric(df["pSymbol"], errors="coerce"),
            "isin": df.get("pISIN", pd.Series([""] * len(df))).astype(str).str.strip(),
            "group": df.get("pGroup", pd.Series([""] * len(df))).astype(str).str.strip(),
        }
    )
    return out.dropna(subset=["token"]).astype({"token": "int64"})


def match_tokens(universe: pd.DataFrame, master: pd.DataFrame) -> pd.DataFrame:
    """Attach a Kotak token to each universe row (by ISIN, then symbol; EQ series preferred)."""
    master = master.assign(_eq=(master["group"] == "EQ").astype(int))
    master = master.sort_values("_eq", ascending=False)
    by_isin = master.drop_duplicates("isin").set_index("isin")["token"]
    by_sym = master.drop_duplicates("symbol").set_index("symbol")["token"]
    tok = universe["isin"].map(by_isin)
    tok = tok.fillna(universe["symbol"].map(by_sym))
    out = universe.assign(token=tok)
    missing = out["token"].isna()
    if missing.any():
        log.warning("No Kotak token for: %s", ", ".join(out.loc[missing, "symbol"]))
    return out[~missing].astype({"token": "int64"}).reset_index(drop=True)


# --------------------------------------------------------------------------- candles

def parse_candles(resp: Any) -> pd.DataFrame:
    """Turn a historical_data() response into date/open/high/low/close/volume rows."""
    data = resp.get("data") if isinstance(resp, dict) else None
    rows: list[list[Any]] = []
    if isinstance(data, dict) and isinstance(data.get("candles"), list):
        rows = [r[:6] for r in data["candles"] if isinstance(r, (list, tuple)) and len(r) >= 6]
    elif isinstance(data, dict) and {"t", "o", "h", "l", "c"} <= set(data):
        # Older parallel-array shape.
        vol = data.get("v") or [0] * len(data["t"])
        rows = [list(x) for x in zip(data["t"], data["o"], data["h"], data["l"], data["c"], vol)]
    if not rows:
        return pd.DataFrame(columns=PRICE_COLS)
    df = pd.DataFrame(rows, columns=PRICE_COLS)
    # Timestamps look like 2026-08-20T00:00:00+0530: the first 10 characters are the date.
    df["date"] = pd.to_datetime(df["date"].astype(str).str[:10])
    for c in PRICE_COLS[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["close"])


def _fault(resp: Any) -> tuple[int | None, str]:
    if not isinstance(resp, dict):
        return 500, f"unexpected response {type(resp).__name__}"
    if str(resp.get("status", "")).lower() == "success":
        return None, ""
    fault = resp.get("fault") or {}
    code = fault.get("code") or resp.get("StatusCode") or resp.get("code")
    msg = fault.get("message") or resp.get("message") or resp.get("Error") or str(resp)[:200]
    try:
        code = int(code)
    except (TypeError, ValueError):
        code = 500
    return code, str(msg)


class History:
    """Fetches and caches daily candles, one CSV per stock."""

    def __init__(self, client: Any, cache_dir: Path, pause_s: float = 0.35,
                 max_retries: int = 5, sleep: Callable[[float], None] = time.sleep):
        self.client = client
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.pause_s = pause_s
        self.max_retries = max_retries
        self.sleep = sleep

    def _call(self, token: int, start: date, end: date) -> pd.DataFrame:
        for attempt in range(self.max_retries):
            resp = self.client.historical_data(
                neosymbol=f"nse_cm|{token}",
                interval="D",
                from_date=start.isoformat(),
                to_date=end.isoformat(),
            )
            self.sleep(self.pause_s)
            code, msg = _fault(resp)
            if code is None:
                return parse_candles(resp)
            if code == 403:
                raise KotakAuthError(f"Kotak rejected the consumer key: {msg}")
            if code == 400:
                # Bad range or no data for this window (e.g. before listing).
                log.debug("token %s %s..%s: %s", token, start, end, msg)
                return pd.DataFrame(columns=PRICE_COLS)
            wait = 2 ** attempt
            if isinstance(resp, dict) and isinstance(resp.get("rateLimit"), dict):
                try:
                    wait = max(wait, float(resp["rateLimit"].get("Retry-After", wait)))
                except ValueError:
                    pass
            log.info("token %s: %s (%s); retrying in %.0fs", token, code, msg, wait)
            self.sleep(wait)
        raise RuntimeError(f"Kotak kept failing for token {token} ({start}..{end})")

    def fetch(self, token: int, start: date, end: date) -> pd.DataFrame:
        """All candles between start and end, walking back in 180-day windows."""
        frames, empties = [], 0
        hi = end
        while hi >= start:
            lo = max(start, hi - timedelta(days=WINDOW_DAYS))
            df = self._call(token, lo, hi)
            if df.empty:
                empties += 1
                if empties >= 2:  # nothing for a year: the stock wasn't listed yet
                    break
            else:
                empties = 0
                frames.append(df)
            hi = lo - timedelta(days=1)
        if not frames:
            return pd.DataFrame(columns=PRICE_COLS)
        out = pd.concat(frames).drop_duplicates("date", keep="last")
        return out.sort_values("date").reset_index(drop=True)

    def path(self, symbol: str) -> Path:
        return self.cache_dir / f"{symbol}.csv"

    def update(self, symbol: str, token: int, years: int, today: date | None = None) -> int:
        """Bring one stock's cache up to date. Returns rows now cached."""
        today = today or date.today()
        p = self.path(symbol)
        old = pd.read_csv(p, parse_dates=["date"]) if p.exists() else None
        if old is not None and not old.empty:
            start = old["date"].max().date() - timedelta(days=7)
        else:
            start = today - timedelta(days=int(365.25 * years))
        new = self.fetch(token, start, today)
        df = new if old is None else pd.concat([old, new]).drop_duplicates("date", keep="last")
        df = df.sort_values("date")
        df.to_csv(p, index=False, date_format="%Y-%m-%d")
        return len(df)
