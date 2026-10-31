"""Run with:  python -m pytest benter_nse/tests -q"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from benter_nse import betting, config, data, kotak, synthetic
from benter_nse import engine


# ----------------------------------------------------------------- Kotak client

class FakeClient:
    """Mimics NeoAPI.historical_data / scrip_master."""

    def __init__(self, listed: date, fail_first: int = 0, code: int = 429):
        self.listed = listed
        self.calls: list[tuple[str, str]] = []
        self.fail_first = fail_first
        self.code = code

    def scrip_master(self, exchange_segment=None):
        return "https://example/nse_cm-v1.csv"

    def historical_data(self, neosymbol, interval, from_date, to_date):
        assert interval == "D" and neosymbol.startswith("nse_cm|")
        lo, hi = date.fromisoformat(from_date), date.fromisoformat(to_date)
        assert (hi - lo).days <= 179, "window longer than Kotak's 180-day limit"
        self.calls.append((from_date, to_date))
        if self.fail_first:
            self.fail_first -= 1
            return {"status": "ERROR", "fault": {"code": self.code, "message": "slow down"},
                    "rateLimit": {"Retry-After": "1"}}
        days = pd.bdate_range(max(lo, self.listed), hi)
        candles = [[f"{d.date()}T00:00:00+0530", 100, 101, 99, 100.5, 1000, 0] for d in days]
        return {"status": "success", "interval": "D", "data": {"candles": candles}}


def test_fetch_walks_back_in_180_day_windows_and_stops_at_listing(tmp_path):
    today = date(2026, 10, 30)
    c = FakeClient(listed=today - timedelta(days=500))
    h = kotak.History(c, tmp_path, pause_s=0, sleep=lambda s: None)
    df = h.fetch(1333, today - timedelta(days=365 * 8), today)
    assert df["date"].min().date() >= c.listed
    assert df["date"].is_monotonic_increasing and df["date"].is_unique
    # 500 days of listing = 3 windows with data, then 2 empty windows and stop.
    assert len(c.calls) == 5


def test_retries_on_rate_limit_then_succeeds(tmp_path):
    waits = []
    c = FakeClient(listed=date(2026, 1, 1), fail_first=2)
    h = kotak.History(c, tmp_path, pause_s=0, sleep=waits.append)
    df = h.fetch(1, date(2026, 6, 1), date(2026, 6, 30))
    assert len(df) > 15 and 1.0 in waits


def test_bad_key_raises(tmp_path):
    c = FakeClient(listed=date(2026, 1, 1), fail_first=1, code=403)
    with pytest.raises(kotak.KotakAuthError):
        kotak.History(c, tmp_path, pause_s=0, sleep=lambda s: None).fetch(1, date(2026, 6, 1), date(2026, 6, 30))


def test_update_is_incremental(tmp_path):
    c = FakeClient(listed=date(2020, 1, 1))
    h = kotak.History(c, tmp_path, pause_s=0, sleep=lambda s: None)
    n1 = h.update("ITC", 1, years=1, today=date(2026, 9, 30))
    c.calls.clear()
    n2 = h.update("ITC", 1, years=1, today=date(2026, 10, 30))
    assert n2 > n1 and len(c.calls) == 1  # only the new month was requested


def test_parse_both_response_shapes():
    new = {"status": "success", "data": {"candles": [["2026-08-20T00:00:00+0530", 1, 2, 0.5, 1.5, 10, 0]]}}
    old = {"status": "success", "data": {"t": ["2026-08-20T00:00:00+0530"], "o": [1], "h": [2],
                                         "l": [0.5], "c": [1.5], "v": [10]}}
    for r in (new, old):
        df = kotak.parse_candles(r)
        assert df.iloc[0]["date"] == pd.Timestamp("2026-08-20") and df.iloc[0]["close"] == 1.5


def test_token_match_prefers_isin_then_eq_series():
    uni = pd.DataFrame({"symbol": ["ITC", "YESBANK"], "company": ["", ""], "industry": ["", ""],
                        "isin": ["INE154A01025", "INE528G01035"]})
    master = pd.DataFrame({"symbol": ["ITC", "YESBANK", "YESBANK"], "trd_symbol": ["", "", ""],
                           "token": [1660, 12900, 11915], "isin": ["INE154A01025", "INE528G01035", "INE528G01035"],
                           "group": ["EQ", "BL", "EQ"]})
    out = kotak.match_tokens(uni, master).set_index("symbol")["token"]
    assert out["ITC"] == 1660 and out["YESBANK"] == 11915


# ----------------------------------------------------------------- data checks

def test_bonus_is_adjusted_but_crash_is_not():
    d = pd.bdate_range("2025-01-01", periods=10)
    close = np.array([200, 202, 204, 206, 208, 105, 106, 107, 66, 67], float)
    opn = np.r_[close[0], [209 * 0.5 if i == 5 else (108 * 0.62 if i == 8 else close[i - 1]) for i in range(1, 10)]]
    df = pd.DataFrame({"date": d, "open": opn, "high": close, "low": close, "close": close, "volume": 1000.0})
    out, notes = data.adjust_corporate_actions(df, "X")
    kinds = {n["kind"] for n in notes}
    assert kinds == {"adjusted", "large gap left as is"}
    assert out.loc[0, "close"] == pytest.approx(100)          # halved by the 1:1 bonus
    assert out.loc[7, "close"] == pytest.approx(107)          # crash not undone


# ----------------------------------------------------------------- betting maths

def test_bar_and_kelly():
    pi, m, k = pd.Series([0.5]), pd.Series([0.06]), 0.0035
    assert betting.bar(pi, m, k)[0] == pytest.approx(0.5 + 0.0035 / 0.12)
    # At the crowd's own odds the bet is worthless: Kelly says bet nothing.
    assert betting.full_kelly(pi, pi, m, 0.0)[0] == pytest.approx(0.0, abs=1e-12)
    # Kelly maximises expected log growth: check against a brute-force search.
    c = pd.Series([0.56])
    x = float(betting.full_kelly(c, pi, m, k)[0])
    a, l = 2 * 0.06 * 0.5 - k, 2 * 0.06 * 0.5 + k
    grid = np.linspace(0, 3 * x, 3001)
    g = 0.56 * np.log1p(grid * a) + 0.44 * np.log1p(-grid * l)
    assert grid[np.argmax(g)] == pytest.approx(x, rel=0.01)


def test_size_caps_and_floors():
    df = pd.DataFrame({"c": [0.7, 0.6, 0.51], "pi": [0.5] * 3, "m": [0.06] * 3})
    out = betting.size(df, 0.0035, 0.25, 0.05, 0.08, min_weight=0.005)
    assert out["weight"].max() <= 0.05 and out["weight"].sum() <= 0.08 + 1e-12
    assert out.loc[2, "call"] == "no bet"


# ----------------------------------------------------------------- end to end

@pytest.mark.parametrize("signal,expected", [(True, {"EDGE", "WEAK EDGE"}),
                                             (False, {"NO EDGE", "NO EDGE AFTER COSTS"})])
def test_pipeline_finds_planted_edge_and_rejects_noise(tmp_path, signal, expected):
    synthetic.make(tmp_path, n_stocks=120, years=7, seed=3, signal=signal)
    r = engine.run(config.load(data_dir=tmp_path))
    assert r["verdict"]["label"] in expected
    if not signal:
        bets = [s for s in r["signals"] if s["call"] == "BET"]
        assert len(bets) <= 3
    assert (tmp_path / "output" / "report.html").exists()
