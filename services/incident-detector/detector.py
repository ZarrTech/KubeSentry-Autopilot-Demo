import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request
from kubernetes import client, config

NAMESPACE = os.getenv("NAMESPACE", "demo-aiops")
DEPLOYMENT_NAME = os.getenv("DEPLOYMENT_NAME", "payments-api")
LOKI_URL = os.getenv("LOKI_URL", "http://loki-gateway.monitoring.svc.cluster.local")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
ERROR_THRESHOLD = int(os.getenv("ERROR_THRESHOLD", "5"))
ROLL_OUT_WINDOW_MINUTES = int(os.getenv("ROLL_OUT_WINDOW_MINUTES", "10"))
METRIC_ERROR_RATE_THRESHOLD = float(os.getenv("METRIC_ERROR_RATE_THRESHOLD", "0.1"))
AUDIT_DIR = Path(os.getenv("AUDIT_DIR", "/var/run/aiops"))

app = Flask(__name__)
app_state: dict[str, Any] = {
    "last_rollout": None,
    "active_incident": None,
    "approved": False,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_k8s_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))


def load_kube() -> tuple[client.AppsV1Api, client.CoreV1Api]:
    try:
        config.load_incluster_config()
    except config.ConfigException:
        config.load_kube_config()
    return client.AppsV1Api(), client.CoreV1Api()


def find_rollout(api: client.AppsV1Api) -> dict[str, Any] | None:
    dep = api.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    condition_ts = None
    for cond in dep.status.conditions or []:
        if cond.type == "Progressing" and cond.status == "True":
            condition_ts = parse_k8s_ts(cond.last_update_time)
    rs_list = api.list_namespaced_replica_set(namespace=NAMESPACE, label_selector=f"app={DEPLOYMENT_NAME}")
    if not rs_list.items:
        return None
    latest = sorted(
        rs_list.items,
        key=lambda i: i.metadata.creation_timestamp or datetime(1970, 1, 1, tzinfo=timezone.utc),
        reverse=True,
    )[0]
    image = latest.spec.template.spec.containers[0].image
    rollout_time = condition_ts or latest.metadata.creation_timestamp
    if rollout_time is None:
        return None
    return {
        "deployment": DEPLOYMENT_NAME,
        "image": image,
        "rollout_time": rollout_time.isoformat(),
        "replicaset": latest.metadata.name,
    }


def query_loki_errors(since: datetime) -> dict[str, Any]:
    query = '{namespace="demo-aiops", app="payments-api"} |= "\"status_code\": 500"'
    params = {
        "query": query,
        "start": str(int(since.timestamp() * 1e9)),
        "limit": 200,
    }
    resp = requests.get(f"{LOKI_URL}/loki/api/v1/query_range", params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()
    entries: list[dict[str, Any]] = []
    for stream in payload.get("data", {}).get("result", []):
        for ts, line in stream.get("values", []):
            entries.append({"ts": ts, "line": line})
    entries.sort(key=lambda x: x["ts"], reverse=True)
    return {"count": len(entries), "samples": entries[:5]}


def query_prometheus_error_rate() -> float:
    query = 'sum(rate(http_requests_total{namespace="demo-aiops",service="payments-api",code=~"5.."}[5m])) / clamp_min(sum(rate(http_requests_total{namespace="demo-aiops",service="payments-api"}[5m])), 1)'
    resp = requests.get(
        f"{PROMETHEUS_URL}/api/v1/query",
        params={"query": query},
        timeout=15,
    )
    resp.raise_for_status()
    result = resp.json().get("data", {}).get("result", [])
    if not result:
        return 0.0
    return float(result[0]["value"][1])


def collect_recent_events(core_api: client.CoreV1Api, since: datetime) -> list[dict[str, str]]:
    events = core_api.list_namespaced_event(NAMESPACE)
    out: list[dict[str, str]] = []
    for event in events.items:
        if event.involved_object.kind not in {"Deployment", "ReplicaSet"}:
            continue
        if event.involved_object.name != DEPLOYMENT_NAME and not event.involved_object.name.startswith(f"{DEPLOYMENT_NAME}-"):
            continue
        event_ts = parse_k8s_ts(event.last_timestamp) or parse_k8s_ts(event.event_time)
        if event_ts and event_ts >= since:
            out.append({
                "reason": event.reason or "",
                "message": event.message or "",
                "type": event.type or "",
                "time": event_ts.isoformat(),
            })
    return out[:5]


def write_incident(incident: dict[str, Any]) -> None:
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    (AUDIT_DIR / f"incident-{stamp}.json").write_text(json.dumps(incident, indent=2))


def post_slack(incident: dict[str, Any]) -> None:
    if not SLACK_WEBHOOK_URL:
        return
    text = (
        f":rotating_light: *{incident['severity']}* deployment regression detected\n"
        f"Service: `{DEPLOYMENT_NAME}` in `{NAMESPACE}`\n"
        f"Image: `{incident['evidence']['image_tag']}`\n"
        f"Rollout time: `{incident['evidence']['rollout_time']}`\n"
        f"500 logs: `{incident['evidence']['error_count_logs']}`\n"
        f"5xx metric rate: `{incident['evidence']['error_rate_metric']}`\n"
        "Recommendation: `kubectl rollout undo deployment/payments-api -n demo-aiops`\n"
        "Status: awaiting approval"
    )
    requests.post(SLACK_WEBHOOK_URL, json={"text": text}, timeout=10).raise_for_status()


def detector_loop() -> None:
    apps_api, core_api = load_kube()
    while True:
        try:
            rollout = find_rollout(apps_api)
            if not rollout:
                time.sleep(POLL_SECONDS)
                continue
            last = app_state.get("last_rollout")
            if not last or last["replicaset"] != rollout["replicaset"]:
                app_state["last_rollout"] = rollout
                app_state["active_incident"] = None
                app_state["approved"] = False

            rollout_time = datetime.fromisoformat(app_state["last_rollout"]["rollout_time"])
            if utcnow() - rollout_time > timedelta(minutes=ROLL_OUT_WINDOW_MINUTES):
                time.sleep(POLL_SECONDS)
                continue

            errors = query_loki_errors(rollout_time)
            error_rate = query_prometheus_error_rate()
            rollout_events = collect_recent_events(core_api, rollout_time)

            has_log_signal = errors["count"] >= ERROR_THRESHOLD
            has_metric_signal = error_rate >= METRIC_ERROR_RATE_THRESHOLD
            has_event_signal = len(rollout_events) > 0

            if has_log_signal and has_metric_signal and has_event_signal and not app_state.get("active_incident"):
                incident = {
                    "id": f"inc-{int(time.time())}",
                    "type": "deployment regression",
                    "severity": "high",
                    "summary": "payments-api shows log+metric failure signals right after rollout",
                    "recommended_action": "rollback",
                    "status": "awaiting approval",
                    "evidence": {
                        "image_tag": rollout["image"],
                        "rollout_time": rollout["rollout_time"],
                        "error_count_logs": errors["count"],
                        "error_rate_metric": error_rate,
                        "rollout_events": rollout_events,
                        "log_samples": errors["samples"],
                    },
                }
                app_state["active_incident"] = incident
                write_incident(incident)
                post_slack(incident)
        except Exception as exc:  # noqa: BLE001
            print(json.dumps({"level": "error", "message": str(exc)}), flush=True)
        time.sleep(POLL_SECONDS)


@app.get("/healthz")
def healthz():
    return jsonify({"status": "ok"})


@app.get("/incident")
def incident():
    return jsonify(app_state.get("active_incident") or {})


@app.post("/approve")
def approve():
    if not app_state.get("active_incident"):
        return jsonify({"error": "no active incident"}), 404
    payload = request.get_json(silent=True) or {}
    approver = payload.get("approver", "demo-user")
    app_state["approved"] = True
    app_state["active_incident"]["status"] = "approved"
    app_state["active_incident"]["approved_by"] = approver
    write_incident({"approval": app_state["active_incident"]})
    return jsonify({"status": "approved", "approver": approver})


if __name__ == "__main__":
    t = threading.Thread(target=detector_loop, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=8081)
