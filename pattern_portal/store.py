"""Loads the latest scan and answers the portal's filter queries."""

from __future__ import annotations

import json
import math
import threading
from collections import OrderedDict

import numpy as np
import pandas as pd

from . import config, universe

QUALITY_MIN = {"any": 0, "fair": 50, "strong": 65, "textbook": 80}
STATUS_BONUS = {"Confirmed": 10, "Forming": 6, "Marginal": 3, "Failed": -12}
FACETS = ("family", "direction", "tf", "status", "quality")


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._sym: OrderedDict[str, dict] = OrderedDict()
        self.reload()

    # ------------------------------------------------------------ loading
    def reload(self) -> None:
        cur = config.SCAN_DIR / "current"
        with self._lock:
            self._sym.clear()
            self.lists = universe.load_lists()
            self.names = universe.load_names()
            if (cur / "index.csv.gz").exists():
                idx = pd.read_csv(cur / "index.csv.gz")
                self.meta = json.loads((cur / "meta.json").read_text())
            else:
                idx = pd.DataFrame(columns=["id", "symbol", "tf", "pattern", "family", "direction",
                                            "status", "quality", "score", "bars_ago"])
                self.meta = {}
            idx["bars_ago"] = pd.to_numeric(idx["bars_ago"], errors="coerce").fillna(10**6)
            idx["score"] = pd.to_numeric(idx["score"], errors="coerce").fillna(0)
            idx["rank"] = (idx["score"] + 30 * np.exp(-idx["bars_ago"] / 10)
                           + idx["status"].map(STATUS_BONUS).fillna(0))
            self.idx = idx

    def symbol(self, sym: str) -> dict | None:
        sym = sym.upper()
        with self._lock:
            if sym in self._sym:
                self._sym.move_to_end(sym)
                return self._sym[sym]
        p = config.SCAN_DIR / "current" / "symbols" / f"{sym}.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text())
        d["by_id"] = {r["id"]: r for r in d["patterns"]}
        d["info"]["name"] = self.names.get(sym, "")
        with self._lock:
            self._sym[sym] = d
            if len(self._sym) > 400:
                self._sym.popitem(last=False)
        return d

    # ------------------------------------------------------------ queries
    def _masks(self, df: pd.DataFrame, f: dict) -> dict[str, pd.Series]:
        m: dict[str, pd.Series] = {}
        if f.get("universe") and f["universe"] != "all":
            m["universe"] = df["symbol"].isin(self.lists.get(f["universe"], set()))
        if f.get("q"):
            q = f["q"].strip().upper()
            hits = {s for s, n in self.names.items() if q in n.upper()}
            m["q"] = df["symbol"].str.contains(q, regex=False) | df["symbol"].isin(hits)
        if f.get("symbol"):
            m["symbol"] = df["symbol"] == f["symbol"].upper()
        within = f.get("within", "30")
        if within not in ("any", "", None):
            m["within"] = df["bars_ago"] <= int(within)
        if f.get("volume"):
            m["volume"] = df["volume_confirmed"].astype(str).str.lower() == "true"
        if f.get("from_book"):
            m["from_book"] = df["score"] >= 80
        for fac in ("family", "direction", "tf", "status", "pattern"):
            vals = f.get(fac)
            if vals:
                m[fac] = df[fac].isin(vals)
        qmin = QUALITY_MIN.get(f.get("quality", "any"), 0)
        if qmin:
            m["quality"] = df["score"] >= qmin
        return m

    @staticmethod
    def _apply(df, masks, skip=None):
        keep = pd.Series(True, index=df.index)
        for k, v in masks.items():
            if k != skip:
                keep &= v
        return df[keep]

    def query(self, f: dict) -> dict:
        df = self.idx
        masks = self._masks(df, f)
        view = self._apply(df, masks)
        facets = {}
        for fac in FACETS:
            sub = self._apply(df, masks, skip=fac)
            if fac == "quality":
                sc = sub["score"]
                facets[fac] = {"any": int(len(sc)), "fair": int((sc >= 50).sum()),
                               "strong": int((sc >= 65).sum()), "textbook": int((sc >= 80).sum())}
            else:
                facets[fac] = {str(k): int(v) for k, v in sub[fac].value_counts().items()}
        sort = f.get("sort", "composite")
        if sort == "recent":
            view = view.sort_values(["bars_ago", "score"], ascending=[True, False])
        elif sort == "cleanest":
            view = view.sort_values(["score", "bars_ago"], ascending=[False, True])
        else:
            view = view.sort_values("rank", ascending=False)
        per = min(int(f.get("per_page", 48)), 200)
        page = max(1, int(f.get("page", 1)))
        items = []
        for row in view.iloc[(page - 1) * per: page * per].itertuples():
            d = self.symbol(row.symbol)
            if d and row.id in d["by_id"]:
                items.append({**d["by_id"][row.id], "name": d["info"]["name"]})
        return {
            "total": int(len(df)),
            "in_view": int(len(view)),
            "confirmed": int((view["status"] == "Confirmed").sum()),
            "bull": int((view["direction"] == "bull").sum()),
            "bear": int((view["direction"] == "bear").sum()),
            "page": page,
            "pages": max(1, math.ceil(len(view) / per)),
            "facets": facets,
            "items": items,
        }

    def stock(self, sym: str) -> dict | None:
        d = self.symbol(sym)
        if d is None:
            return None
        pats = d["patterns"]
        return {
            "info": d["info"],
            "summary": {
                "patterns": len(pats),
                "timeframes": sorted({p["tf"] for p in pats}, key=["1D", "1W", "1M"].index),
                "by_tf": {tf: sum(p["tf"] == tf for p in pats) for tf in ("1D", "1W", "1M")},
                "confirmed": sum(p["status"] == "Confirmed" for p in pats),
                "held": sum(p["status"] == "Confirmed" and p.get("outcome") != "Stopped" for p in pats),
                "forming": sum(p["status"] == "Forming" for p in pats),
                "target_hit": sum(p.get("outcome") == "Target hit" for p in pats),
                "stopped": sum(p.get("outcome") == "Stopped" for p in pats),
            },
            "patterns": sorted(pats, key=lambda p: p["event_date"], reverse=True),
        }
