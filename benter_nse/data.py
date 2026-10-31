"""Load cached candles into date x symbol panels, with a split/bonus guard."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# Price ratios produced by common splits, bonuses and consolidations.
_STD_RATIOS = np.array(
    [1 / 10, 1 / 5, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 4, 4 / 5, 2, 5, 10]
)
_GAP_LO, _GAP_HI = 0.70, 1.45  # beyond NSE's 20% circuit by a wide margin


@dataclass
class Panel:
    open: pd.DataFrame
    high: pd.DataFrame
    low: pd.DataFrame
    close: pd.DataFrame
    volume: pd.DataFrame
    adjustments: pd.DataFrame  # symbol, date, factor, kind

    @property
    def symbols(self) -> list[str]:
        return list(self.close.columns)


def adjust_corporate_actions(df: pd.DataFrame, symbol: str) -> tuple[pd.DataFrame, list[dict]]:
    """Back-adjust prices across overnight gaps that match a split/bonus ratio.

    If Kotak's candles are already adjusted there are no such gaps and nothing
    changes. Large gaps that do not match a standard ratio are left alone and
    reported, so a real crash is never mistaken for a split.
    """
    df = df.sort_values("date").reset_index(drop=True)
    notes: list[dict] = []
    if len(df) < 2:
        return df, notes
    gap = (df["open"] / df["close"].shift(1)).to_numpy()
    day = (df["close"] / df["close"].shift(1)).to_numpy()
    for i in np.where((gap < _GAP_LO) | (gap > _GAP_HI))[0]:
        g = gap[i]
        j = int(np.argmin(np.abs(np.log(_STD_RATIOS) - np.log(g))))
        ratio = _STD_RATIOS[j]
        when = df.at[i, "date"]
        # The open must sit on a standard ratio and the close must confirm it
        # (a lone bad opening tick would not carry through to the close).
        if abs(np.log(g / ratio)) < 0.06 and abs(np.log(day[i] / ratio)) < 0.25:
            cols = ["open", "high", "low", "close"]
            df.loc[: i - 1, cols] = df.loc[: i - 1, cols] * ratio
            df.loc[: i - 1, "volume"] = df.loc[: i - 1, "volume"] / ratio
            notes.append({"symbol": symbol, "date": when, "factor": round(float(ratio), 4),
                          "kind": "adjusted"})
        else:
            notes.append({"symbol": symbol, "date": when, "factor": round(float(g), 4),
                          "kind": "large gap left as is"})
    return df, notes


def load_panel(prices_dir: Path, symbols: list[str] | None = None) -> Panel:
    files = sorted(Path(prices_dir).glob("*.csv"))
    if symbols is not None:
        want = set(symbols)
        files = [f for f in files if f.stem in want]
    if not files:
        raise FileNotFoundError(f"No price files in {prices_dir}. Run `python -m benter_nse fetch`.")
    fields: dict[str, dict[str, pd.Series]] = {k: {} for k in ["open", "high", "low", "close", "volume"]}
    notes: list[dict] = []
    for f in files:
        df = pd.read_csv(f, parse_dates=["date"])
        df = df.dropna(subset=["close"])
        df = df[df["close"] > 0]
        if len(df) < 30:
            continue
        df, n = adjust_corporate_actions(df, f.stem)
        notes += n
        df = df.drop_duplicates("date", keep="last").set_index("date")
        for k in fields:
            fields[k][f.stem] = df[k].astype(float)
    wide = {k: pd.DataFrame(v).sort_index() for k, v in fields.items()}
    # Drop calendar days that almost no stock traded (stray weekend candles).
    active = wide["close"].notna().mean(axis=1)
    keep = active >= 0.2
    wide = {k: v.loc[keep] for k, v in wide.items()}
    # Carry prices through short suspensions only.
    for k in ["open", "high", "low", "close"]:
        wide[k] = wide[k].ffill(limit=5)
    wide["volume"] = wide["volume"].fillna(0.0)
    adj = pd.DataFrame(notes, columns=["symbol", "date", "factor", "kind"])
    if len(adj):
        log.info("Corporate-action check: %d adjusted, %d large gaps left as is",
                 (adj["kind"] == "adjusted").sum(), (adj["kind"] != "adjusted").sum())
    return Panel(adjustments=adj, **wide)
