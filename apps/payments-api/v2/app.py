import json
import os
from datetime import datetime, timezone

from flask import Flask, Response, jsonify
from prometheus_client import CONTENT_TYPE_LATEST, Counter, generate_latest

app = Flask(__name__)
VERSION = os.getenv("APP_VERSION", "v2")
REQUEST_COUNTER = Counter("http_requests_total", "HTTP requests", ["service", "namespace", "code"])


def emit_log(status: int, message: str) -> None:
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "service": "payments-api",
        "version": VERSION,
        "status_code": status,
        "message": message,
    }
    print(json.dumps(payload), flush=True)


@app.get("/healthz")
def healthz():
    REQUEST_COUNTER.labels(service="payments-api", namespace="demo-aiops", code="200").inc()
    emit_log(200, "health check")
    return jsonify({"status": "ok", "version": VERSION})


@app.get("/metrics")
def metrics():
    return Response(generate_latest(), mimetype=CONTENT_TYPE_LATEST)


@app.get("/pay")
def pay():
    gateway_token = os.getenv("PAYMENTS_GATEWAY_TOKEN")
    if not gateway_token:
        REQUEST_COUNTER.labels(service="payments-api", namespace="demo-aiops", code="500").inc()
        emit_log(500, "missing PAYMENTS_GATEWAY_TOKEN")
        return jsonify({"status": "error", "error": "configuration missing", "version": VERSION}), 500

    REQUEST_COUNTER.labels(service="payments-api", namespace="demo-aiops", code="200").inc()
    emit_log(200, "payment accepted")
    return jsonify({"status": "paid", "version": VERSION}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
