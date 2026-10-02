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
    assert any(r["pattern"] == "Bear Flag" for r in analyze(path(knots), "1D"))


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
