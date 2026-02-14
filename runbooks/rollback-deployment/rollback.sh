#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${NAMESPACE:-demo-aiops}"
DEPLOYMENT="${DEPLOYMENT:-payments-api}"
INCIDENT_ENDPOINT="${INCIDENT_ENDPOINT:-http://incident-detector.demo-aiops.svc.cluster.local:8081/incident}"
SERVICE_ENDPOINT="${SERVICE_ENDPOINT:-http://payments-api.demo-aiops.svc.cluster.local/pay}"
AUDIT_FILE="${AUDIT_FILE:-runbooks/rollback-deployment/audit-record.json}"
MAX_WAIT_SECONDS="${MAX_WAIT_SECONDS:-180}"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*"; }

log "Fetching incident context"
incident_json="$(curl -fsS "$INCIDENT_ENDPOINT")"
if [[ "$incident_json" == "{}" ]]; then
  log "No active incident found. Exiting."
  exit 1
fi

incident_status="$(echo "$incident_json" | jq -r '.status')"
if [[ "$incident_status" != "approved" ]]; then
  log "Incident is not approved (status=$incident_status). Exiting."
  exit 1
fi

log "Checking previous ReplicaSet existence"
rs_count="$(kubectl -n "$NAMESPACE" get rs -l app="$DEPLOYMENT" --no-headers | wc -l | tr -d ' ')"
if (( rs_count < 2 )); then
  log "Need at least two ReplicaSets for safe rollback."
  exit 1
fi

rollout_time="$(echo "$incident_json" | jq -r '.evidence.rollout_time')"
if [[ "$rollout_time" == "null" ]]; then
  log "Rollout time missing from incident evidence"
  exit 1
fi

rollout_epoch="$(date -d "$rollout_time" +%s)"
now_epoch="$(date -u +%s)"
age_seconds=$(( now_epoch - rollout_epoch ))
if (( age_seconds > 3600 )); then
  log "Rollback policy violation: rollout older than 60 minutes ($age_seconds sec)."
  exit 1
fi

log "Executing rollback"
kubectl -n "$NAMESPACE" rollout undo "deployment/$DEPLOYMENT"
kubectl -n "$NAMESPACE" rollout status "deployment/$DEPLOYMENT" --timeout=120s

log "Validating service recovery"
start_time=$(date +%s)
validation_status="failed"
while true; do
  code="$(curl -s -o /tmp/pay.out -w '%{http_code}' "$SERVICE_ENDPOINT" || true)"
  if [[ "$code" == "200" ]]; then
    validation_status="passed"
    break
  fi
  now=$(date +%s)
  if (( now - start_time > MAX_WAIT_SECONDS )); then
    break
  fi
  sleep 5
done

log "Collecting post-rollback 5xx count from logs"
errors_5m="$(kubectl -n "$NAMESPACE" logs deploy/$DEPLOYMENT --since=5m | rg '"status_code": 500' -c || true)"
errors_5m="${errors_5m:-0}"

cat > "$AUDIT_FILE" <<JSON
{
  "timestamp": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "namespace": "$NAMESPACE",
  "deployment": "$DEPLOYMENT",
  "incident": $incident_json,
  "action": "kubectl rollout undo deployment/$DEPLOYMENT -n $NAMESPACE",
  "validation": {
    "http_status": "$validation_status",
    "post_rollback_5xx_logs_5m": $errors_5m
  }
}
JSON

if [[ "$validation_status" != "passed" ]]; then
  log "Rollback ran but validation failed. Audit: $AUDIT_FILE"
  exit 1
fi

log "Rollback complete and validated. Audit: $AUDIT_FILE"
