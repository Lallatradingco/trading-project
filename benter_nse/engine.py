"""Run the whole pipeline: prices -> factors -> two stages -> backtest -> today's calls."""

from __future__ import annotations

import json
import logging
import pickle
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from . import betting, data, features, model, universe
from .config import Config

log = logging.getLogger(__name__)


def _clean(x):
    """JSON-safe numbers."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if not np.isfinite(x) else round(float(x), 6)
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (pd.Timestamp, datetime)):
        return str(x.date()) if hasattr(x, "date") else str(x)
    return x


def _signal_table(today: pd.DataFrame, uni: pd.DataFrame, cfg: Config) -> pd.DataFrame:
    sized = betting.size(today, cfg.cost, cfg.kelly_fraction, cfg.max_weight, cfg.max_gross,
                         cfg.min_weight)
    names = uni.set_index("symbol")["company"]
    sized["company"] = sized["symbol"].map(names).fillna(sized["symbol"])
    cols = ["symbol", "company", "industry", "close", "f", "pi", "c", "bar", "edge",
            "exp_net", "kelly_full", "weight", "call", "rsi_14", "mom_12_1", "rev_1m",
            "vol_60", "bull_div", "turnover_cr"]
    return sized[cols].sort_values("edge", ascending=False).reset_index(drop=True)


def run(cfg: Config) -> dict:
    uni = universe.load(cfg.index_csv)
    panel = data.load_panel(cfg.prices_dir, list(uni["symbol"]))
    log.info("Loaded %d stocks, %s to %s", len(panel.symbols),
             panel.close.index[0].date(), panel.close.index[-1].date())
    industry = uni.set_index("symbol")["industry"]
    rows = features.build(panel, industry, cfg.horizon_days, cfg.sample_every_days,
                          cfg.min_turnover_cr)
    fit = model.walk_forward(rows, cfg.horizon_days, cfg.min_train_years, cfg.l2_C)
    bt = betting.backtest(fit.rows, cfg.horizon_days, cfg.sample_every_days, cfg.cost,
                          cfg.kelly_fraction, cfg.max_weight, cfg.max_gross, cfg.min_weight)
    label, explanation = betting.verdict(fit.summary, bt)

    latest = rows["date"].max()
    today = model.predict(rows[rows["date"] == latest], fit.stage1, fit.stage2, cfg.horizon_days)
    table = _signal_table(today, uni, cfg)

    out = cfg.out_dir
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "signals.csv", index=False)
    bt["periods"].to_csv(out / "backtest_periods.csv", index=False)
    fit.yearly.to_csv(out / "walk_forward_years.csv", index=False)
    panel.adjustments.to_csv(out / "corporate_actions.csv", index=False)
    with open(out / "model.pkl", "wb") as fh:
        pickle.dump({"stage1": fit.stage1, "stage2": fit.stage2,
                     "features": features.FEATURES, "trained": str(latest.date())}, fh)

    result = {
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(latest.date()),
        "settings": {
            "horizon_days": cfg.horizon_days, "round_trip_bps": cfg.round_trip_bps,
            "kelly_fraction": cfg.kelly_fraction, "max_weight": cfg.max_weight,
            "max_gross": cfg.max_gross, "min_turnover_cr": cfg.min_turnover_cr,
        },
        "universe": {"listed": int(len(uni)), "with_prices": int(len(panel.symbols)),
                     "eligible_today": int(len(today))},
        "verdict": {"label": label, "text": explanation},
        "summary": fit.summary,
        "yearly": fit.yearly.to_dict(orient="records"),
        "backtest": {"kelly": bt["kelly"], "naive": bt["naive"],
                     "curve": bt["periods"][["date", "eq_kelly", "eq_naive"]]
                     .assign(date=lambda d: d["date"].dt.strftime("%Y-%m-%d"))
                     .to_dict(orient="records") if len(bt["periods"]) else []},
        "coefficients": fit.coefs[["label", "weight"]].to_dict(orient="records"),
        "corporate_actions": panel.adjustments.assign(
            date=lambda d: pd.to_datetime(d["date"]).dt.strftime("%Y-%m-%d") if len(d) else d["date"]
        ).to_dict(orient="records"),
        "signals": table.to_dict(orient="records"),
    }
    return _save(cfg, _clean(result))


def _save(cfg: Config, result: dict) -> dict:
    from . import report

    out = cfg.out_dir
    (out / "result.json").write_text(json.dumps(result, indent=1))
    (out / "report.html").write_text(report.render(result))
    log.info("Verdict: %s. Wrote %s", result["verdict"]["label"], out / "report.html")
    return result


def refresh_signals(cfg: Config) -> dict:
    """Today's calls from the last trained model, without re-running the walk-forward."""
    out = cfg.out_dir
    if not (out / "model.pkl").exists() or not (out / "result.json").exists():
        raise FileNotFoundError("No trained model yet. Run `python -m benter_nse run` first.")
    with open(out / "model.pkl", "rb") as fh:
        saved = pickle.load(fh)
    uni = universe.load(cfg.index_csv)
    panel = data.load_panel(cfg.prices_dir, list(uni["symbol"]))
    rows = features.build(panel, uni.set_index("symbol")["industry"], cfg.horizon_days,
                          cfg.sample_every_days, cfg.min_turnover_cr)
    latest = rows["date"].max()
    today = model.predict(rows[rows["date"] == latest], saved["stage1"], saved["stage2"],
                          cfg.horizon_days)
    table = _signal_table(today, uni, cfg)
    table.to_csv(out / "signals.csv", index=False)
    result = json.loads((out / "result.json").read_text())
    result.update({
        "generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "as_of": str(latest.date()),
        "signals": table.to_dict(orient="records"),
    })
    result["universe"]["eligible_today"] = int(len(today))
    return _save(cfg, _clean(result))
