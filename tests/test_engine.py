"""Each detector must find its pattern in a synthetic price path built to contain it."""
import pytest

from pattern_portal.data import resample
from pattern_portal.engine import analyze
from tests.synth import CASES, case, path

ACCEPT = {"Bull Flag": {"Bull Flag", "Bull Pennant"}}


@pytest.mark.parametrize("name", list(CASES))
def test_detects_known_pattern(name):
    found = {r["pattern"] for r in analyze(case(name), "1D")}
    assert found & ACCEPT.get(name, {name}), f"{name} not found; got {found}"


def test_mirrored_flag_is_bearish():
    knots = [(x, 250 - y) for x, y in CASES["Bull Flag"]]
    assert any(r["pattern"] in ("Bear Flag", "Bear Pennant") for r in analyze(path(knots), "1D"))


def test_breakout_levels_and_status():
    r = next(r for r in analyze(case("Double Bottom"), "1D") if r["pattern"] == "Double Bottom")
    assert r["direction"] == "bull" and r["status"] == "Confirmed"
    assert r["target"] > r["breakout"] > r["stop"]
    assert r["rr"] and r["rr"] > 0


def test_forming_pattern_has_no_breakout():
    df = case("Ascending Triangle").iloc[:88]        # cut off before the breakout
    r = [r for r in analyze(df, "1D") if r["pattern"] == "Ascending Triangle"]
    assert r and r[-1]["status"] == "Forming" and r[-1]["breakout_idx"] is None


def test_resample_weekly_monthly():
    df = case("Rectangle")
    w, m = resample(df, "1W"), resample(df, "1M")
    assert len(w) < len(df) and len(m) < len(w)
    assert w["Close"].iloc[-1] == df["Close"].iloc[-1]


def test_flag_detection_is_mirror_symmetric():
    bull = [(r["pattern"].split()[1], r["start"], r["end"], r["status"])
            for r in analyze(case("Bull Flag"), "1D") if r["family"] == "Continuation"]
    knots = [(x, 250 - y) for x, y in CASES["Bull Flag"]]
    bear = [(r["pattern"].split()[1], r["start"], r["end"], r["status"])
            for r in analyze(path(knots), "1D") if r["family"] == "Continuation"]
    assert [b[0] for b in bull] == [b[0] for b in bear]


def test_levels_are_consistent_on_real_shapes():
    for name in CASES:
        for r in analyze(case(name), "1D"):
            if r["direction"] == "bull" and r["stop"] is not None:
                assert r["stop"] < r["breakout"]
            if r["direction"] == "bear" and r["stop"] is not None:
                assert r["stop"] > r["breakout"]
            x = r["chart"]["x"]
            assert len(x) == len(r["chart"]["c"]) and x == sorted(x)
            assert x[0] <= r["start"] and x[-1] <= len(case(name)) - 1
            assert all(i >= x[0] for i, _, _ in r["points"])


def test_no_pattern_ever_reports_a_non_positive_level():
    import glob
    import pandas as pd
    for f in sorted(glob.glob("/home/claude/sample/*.csv"))[:4] or []:
        df = pd.read_csv(f, parse_dates=["Date"], index_col="Date")[["Open", "High", "Low", "Close", "Volume"]]
        for tf in ("1D", "1W", "1M"):
            for r in analyze(resample(df, tf), tf):
                for k in ("breakout", "target", "stop", "range_up", "range_dn"):
                    assert r[k] is None or r[k] > 0, (f, tf, r["id"] if "id" in r else r["pattern"], k, r[k])
