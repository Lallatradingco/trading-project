"""Standalone portal for the engine (its own port; existing Lalla Hub portals untouched)."""

from __future__ import annotations

import json

from flask import Flask, Response, abort, send_file

from . import report
from .config import Config


def create_app(cfg: Config) -> Flask:
    app = Flask(__name__)
    result_path = cfg.out_dir / "result.json"

    def _result() -> dict:
        if not result_path.exists():
            abort(404, "No results yet. Run `python -m benter_nse run` first.")
        return json.loads(result_path.read_text())

    @app.get("/")
    def index() -> Response:
        return Response(report.render(_result()), mimetype="text/html")

    @app.get("/api/result")
    def api_result() -> Response:
        return Response(json.dumps(_result()), mimetype="application/json")

    @app.get("/signals.csv")
    def signals_csv():
        p = cfg.out_dir / "signals.csv"
        if not p.exists():
            abort(404)
        return send_file(p, mimetype="text/csv", as_attachment=True, download_name="benter_signals.csv")

    @app.get("/health")
    def health() -> dict:
        return {"ok": result_path.exists()}

    return app


def serve(cfg: Config, host: str = "127.0.0.1", port: int | None = None) -> None:
    create_app(cfg).run(host=host, port=port or cfg.port, debug=False)
