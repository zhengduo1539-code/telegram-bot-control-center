"""Small HTTP health server used by Render and uptime monitors."""
from __future__ import annotations
import logging
import threading
from flask import Flask, jsonify

logger = logging.getLogger(__name__)
app = Flask(__name__)
_state = {"database": False, "scheduler": False}
_state_lock = threading.Lock()


def set_readiness(*, database: bool | None = None, scheduler: bool | None = None) -> None:
    with _state_lock:
        if database is not None:
            _state["database"] = database
        if scheduler is not None:
            _state["scheduler"] = scheduler


@app.route("/", methods=["GET", "HEAD"])
def home():
    return jsonify({"service": "Schedule Bot", "status": "alive"}), 200


@app.route("/healthz", methods=["GET", "HEAD"])
def healthz():
    return jsonify({"service": "Schedule Bot", "status": "ok"}), 200


@app.route("/health", methods=["GET", "HEAD"])
def health():
    with _state_lock:
        state = dict(_state)
    ready = state["database"] and state["scheduler"]
    return jsonify({"service": "Schedule Bot", "ready": ready, **state}), (200 if ready else 503)


def run_server(port: int = 8080) -> None:
    logger.info("Starting health server on 0.0.0.0:%d", port)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False, threaded=True)


def start_keep_alive(port: int = 8080) -> threading.Thread:
    thread = threading.Thread(target=run_server, args=(port,), daemon=True, name="health-server")
    thread.start()
    logger.info("Health server thread started.")
    return thread
