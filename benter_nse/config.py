"""Load settings from config.toml next to this file."""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

PKG_DIR = Path(__file__).resolve().parent


@dataclass(frozen=True)
class Config:
    index_csv: Path
    index_csv_url: str
    min_turnover_cr: float
    data_dir: Path
    years: int
    request_pause_s: float
    horizon_days: int
    sample_every_days: int
    min_train_years: int
    l2_C: float
    round_trip_bps: float
    kelly_fraction: float
    max_weight: float
    max_gross: float
    min_weight: float
    port: int

    @property
    def prices_dir(self) -> Path:
        return self.data_dir / "prices"

    @property
    def out_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def cost(self) -> float:
        """Round-trip cost as a fraction (35 bps -> 0.0035)."""
        return self.round_trip_bps / 10_000


def load(path: Path | None = None, data_dir: Path | None = None) -> Config:
    path = path or PKG_DIR / "config.toml"
    raw = tomllib.loads(path.read_text())
    base = path.parent
    ddir = Path(data_dir) if data_dir else base / raw["data"]["dir"]
    idx = raw["universe"]["index_csv"]
    # The index CSV lives inside the data folder by default; follow data_dir overrides.
    idx_path = ddir / Path(idx).name if data_dir else base / idx
    return Config(
        index_csv=idx_path,
        index_csv_url=raw["universe"]["index_csv_url"],
        min_turnover_cr=float(raw["universe"]["min_turnover_cr"]),
        data_dir=ddir,
        years=int(raw["data"]["years"]),
        request_pause_s=float(raw["data"]["request_pause_s"]),
        horizon_days=int(raw["model"]["horizon_days"]),
        sample_every_days=int(raw["model"]["sample_every_days"]),
        min_train_years=int(raw["model"]["min_train_years"]),
        l2_C=float(raw["model"]["l2_C"]),
        round_trip_bps=float(raw["costs"]["round_trip_bps"]),
        kelly_fraction=float(raw["sizing"]["kelly_fraction"]),
        max_weight=float(raw["sizing"]["max_weight"]),
        max_gross=float(raw["sizing"]["max_gross"]),
        min_weight=float(raw["sizing"].get("min_weight", 0.0)),
        port=int(raw["portal"]["port"]),
    )
