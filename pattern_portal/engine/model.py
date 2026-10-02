"""Shared data structures for the pattern engine."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class Line:
    """Straight line in (bar index, price) space: price = m * idx + b."""

    m: float
    b: float

    def at(self, x: float) -> float:
        return self.m * x + self.b

    @staticmethod
    def through(x1: float, y1: float, x2: float, y2: float) -> "Line":
        m = (y2 - y1) / (x2 - x1) if x2 != x1 else 0.0
        return Line(m, y1 - m * x1)

    @staticmethod
    def flat(y: float) -> "Line":
        return Line(0.0, y)


FAMILIES = {
    "Double Top": "Reversal",
    "Double Bottom": "Reversal",
    "Triple Top": "Reversal",
    "Triple Bottom": "Reversal",
    "Head & Shoulders": "Reversal",
    "Inverse Head & Shoulders": "Reversal",
    "Rising Wedge": "Reversal",
    "Falling Wedge": "Reversal",
    "Ascending Triangle": "Continuation",
    "Descending Triangle": "Continuation",
    "Symmetrical Triangle": "Continuation",
    "Bull Flag": "Continuation",
    "Bear Flag": "Continuation",
    "Bull Pennant": "Continuation",
    "Bear Pennant": "Continuation",
    "Rectangle": "Range",
    "Ascending Channel": "Range",
    "Descending Channel": "Range",
    "Broadening Formation": "Range",
    "Cup & Handle": "Curve & Cup",
    "Inverted Cup & Handle": "Curve & Cup",
    "Rounding Bottom": "Curve & Cup",
    "Rounding Top": "Curve & Cup",
}


@dataclass
class Candidate:
    pattern: str
    bias: str                 # "bull", "bear" or "neutral" (two-sided until it breaks)
    start: int
    end: int                  # last bar of the formation; breakouts are looked for after it
    score: float              # 0..1 shape quality (how textbook the drawing is)
    up: Optional[Line] = None         # a decisive close above completes it bullishly
    dn: Optional[Line] = None         # a decisive close below completes it bearishly
    height_up: float = 0.0            # measured move used for the bullish target
    height_dn: float = 0.0
    stop_up: Optional[float] = None   # stop for a bullish break (close below before breakout = invalid)
    stop_dn: Optional[float] = None
    expiry: int = 0                   # last bar on which a breakout still counts
    points: list = field(default_factory=list)    # (idx, price, label)
    segments: list = field(default_factory=list)  # dicts: kind, x1, y1, x2, y2
    scale: float = 0.0
    note: str = ""
    line_x0: Optional[int] = None      # where to start drawing the breakout line(s)
    target_up: Optional[float] = None  # fixed target price (overrides the measured move)
    target_dn: Optional[float] = None

    @property
    def family(self) -> str:
        return FAMILIES[self.pattern]
