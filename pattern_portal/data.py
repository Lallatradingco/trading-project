"""Daily NSE candles: download, cache, top up and resample.

History comes from the public eod2_data repository on GitHub (NSE bhavcopy
data, adjusted for splits and bonuses, refreshed weekly). After the first full
download only the tail of each file is fetched (an HTTP range request), so a
weekly refresh of ~2,000 stocks moves a few MB. The days since the last weekly
refresh are topped up from Yahoo Finance when `yfinance` is installed.
"""

from __future__ import annotations

import io
import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, Optional

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)
COLS = ["Open", "High", "Low", "Close", "Volume"]
IST = timezone(timedelta(hours=5, minutes=30))


def _path(sym: str):
    return config.OHLC_DIR / f"{sym.upper()}.csv.gz"


def load(sym: str) -> Optional[pd.DataFrame]:
    p = _path(sym)
    if not p.exists():
        return None
    df = pd.read_csv(p, parse_dates=["Date"], index_col="Date")
    return df[COLS]


def save(sym: str, df: pd.DataFrame) -> None:
    df = df[~df.index.duplicated(keep="last")].sort_index()
    df[COLS].to_csv(_path(sym), index_label="Date", compression="gzip")


def cached_symbols() -> list[str]:
    return sorted(p.name[:-7] for p in config.OHLC_DIR.glob("*.csv.gz"))


def _parse(text: str, header: bool = True) -> pd.DataFrame:
    if header:
        df = pd.read_csv(io.StringIO(text))
    else:
        names = ["Date", "Open", "High", "Low", "Close", "Volume"]
        df = pd.read_csv(io.StringIO(text), header=None)
        df = df.iloc[:, :6]
        df.columns = names
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date", "Close"]).set_index("Date").sort_index()
    for c in COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df[COLS]


# ------------------------------------------------------------------ symbols

def eod2_symbols(session: requests.Session) -> list[str]:
    """Every symbol the eod2 repository has a daily file for."""
    cache = config.META_DIR / "eod2_symbols.txt"
    try:
        r = session.get(config.EOD2_TREE, timeout=60)
        r.raise_for_status()
        tree = r.json()["tree"]
        syms = sorted(t["path"][6:-4].upper() for t in tree
                      if t["path"].startswith("daily/") and t["path"].endswith(".csv"))
        if syms:
            cache.write_text("\n".join(syms))
            return syms
    except Exception as e:  # noqa: BLE001
        log.warning("eod2 file list unavailable (%s); trying isin.csv", e)
    try:
        r = session.get(f"{config.EOD2_RAW}/isin.csv", timeout=60)
        r.raise_for_status()
        syms = sorted(set(pd.read_csv(io.StringIO(r.text))["SYMBOL"].astype(str).str.upper()))
        cache.write_text("\n".join(syms))
        return syms
    except Exception as e:  # noqa: BLE001
        log.warning("isin.csv unavailable (%s)", e)
    return cache.read_text().split() if cache.exists() else []


def refresh_holidays(session: requests.Session) -> None:
    try:
        r = session.get(f"{config.EOD2_RAW}/meta.json", timeout=30)
        r.raise_for_status()
        meta = r.json()
        days = [datetime.strptime(d, "%d-%b-%Y").strftime("%Y-%m-%d") for d in meta.get("holidays", {})]
        (config.META_DIR / "holidays.json").write_text(json.dumps(sorted(days)))
    except Exception as e:  # noqa: BLE001
        log.warning("holiday list unavailable: %s", e)


# ------------------------------------------------------------------ sync

def sync_symbol(session: requests.Session, sym: str) -> str:
    """Bring one symbol's cache up to date. Returns new/updated/same/missing/error."""
    url = f"{config.EOD2_RAW}/daily/{sym.lower()}.csv"
    cached = load(sym)
    try:
        if cached is not None and len(cached) > 30:
            # identity encoding: a byte range of a gzip stream can't be decoded
            r = session.get(url, headers={"Range": "bytes=-6000", "Accept-Encoding": "identity"},
                            timeout=30)
            if r.status_code == 404:
                return "missing"
            if r.status_code == 416:          # file shorter than the range: fetch it whole
                r = session.get(url, timeout=60)
            r.raise_for_status()
            if r.status_code == 206:
                text = r.text.split("\n", 1)[1] if "\n" in r.text else ""
                tail = _parse(text, header=False) if text.strip() else cached.iloc[:0]
                if tail.empty:
                    return "same"
                last = cached.index[-1]
                both = tail.index.intersection(cached.index)
                adjusted = False
                if len(both):
                    a, b = tail.loc[both, "Close"], cached.loc[both, "Close"]
                    adjusted = bool(((a - b).abs() / b > 0.005).any())
                if not adjusted and tail.index[0] <= last:
                    new = tail[tail.index > last]
                    if new.empty:
                        return "same"
                    save(sym, pd.concat([cached, new]))
                    return "updated"
                # split/bonus re-adjusted the history, or too many new rows: full refresh
                r = session.get(url, timeout=60)
        else:
            r = session.get(url, timeout=60)
        if r.status_code == 404:
            return "missing"
        r.raise_for_status()
        df = _parse(r.text)
        if df.empty:
            return "error"
        if cached is not None and len(df) == len(cached) and df.index[-1] == cached.index[-1]:
            return "same"
        save(sym, df)
        return "new" if cached is None else "updated"
    except Exception as e:  # noqa: BLE001
        log.warning("%s: %s", sym, e)
        return "error"


def sync(symbols: Iterable[str], workers: int = 12,
         progress: Optional[Callable[[int, int], None]] = None) -> dict:
    config.ensure_dirs()
    symbols = list(symbols)
    stats: dict[str, int] = {}
    with requests.Session() as s:
        s.headers.update(config.BROWSER_HEADERS)
        adapter = requests.adapters.HTTPAdapter(pool_connections=workers, pool_maxsize=workers)
        s.mount("https://", adapter)
        with ThreadPoolExecutor(workers) as ex:
            futs = {ex.submit(sync_symbol, s, sym): sym for sym in symbols}
            for i, f in enumerate(as_completed(futs), 1):
                st = f.result()
                stats[st] = stats.get(st, 0) + 1
                if progress:
                    progress(i, len(symbols))
    return stats


def topup_yahoo(symbols: Iterable[str], progress: Optional[Callable[[int, int], None]] = None) -> int:
    """Append the latest completed sessions from Yahoo Finance (optional)."""
    try:
        import yfinance as yf
    except ImportError:
        log.info("yfinance not installed; skipping top-up (data stays at the weekly eod2 date)")
        return 0
    now = datetime.now(IST)
    today = pd.Timestamp(now.date())
    include_today = now.hour * 60 + now.minute >= 15 * 60 + 45   # only completed sessions
    symbols = list(symbols)
    added = 0
    for start in range(0, len(symbols), 100):
        chunk = symbols[start:start + 100]
        tickers = [f"{s.removesuffix('_SME')}.NS" for s in chunk]
        try:
            raw = yf.download(tickers, period="1mo", interval="1d", group_by="ticker",
                              auto_adjust=False, threads=True, progress=False)
        except Exception as e:  # noqa: BLE001
            log.warning("yahoo top-up failed for a batch: %s", e)
            continue
        for sym, tk in zip(chunk, tickers):
            try:
                d = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
                d = d[COLS].dropna(subset=["Close"])
            except Exception:  # noqa: BLE001
                continue
            d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
            if not include_today:
                d = d[d.index < today]
            cached = load(sym)
            if cached is None or d.empty:
                continue
            new = d[d.index > cached.index[-1]]
            if len(new):
                save(sym, pd.concat([cached, new]))
                added += 1
        if progress:
            progress(min(start + 100, len(symbols)), len(symbols))
    return added


# ------------------------------------------------------------------ timeframes

def resample(df: pd.DataFrame, tf: str) -> pd.DataFrame:
    if tf == "1D":
        return df
    rule = "W-FRI" if tf == "1W" else "ME"
    agg = {"Open": "first", "High": "max", "Low": "min", "Close": "last", "Volume": "sum"}
    try:
        out = df.resample(rule).agg(agg)
        last = df.index.to_series().resample(rule).last()
    except ValueError:                       # pandas < 2.2 spells month-end "M"
        rule = "M"
        out = df.resample(rule).agg(agg)
        last = df.index.to_series().resample(rule).last()
    out = out.dropna(subset=["Close"])
    out.index = pd.DatetimeIndex(last.loc[out.index].values)   # label = last trading day
    return out
