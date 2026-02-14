# Demo Script: KubeSentry Autopilot Demo

This script runs the full workflow end-to-end and is designed to be repeatable **twice** without manual debugging.

## 0) Prerequisites

- kubeadm cluster reachable via current `kubectl` context.
- `helm`, `jq`, `curl`, `rg` installed locally.
- Container images available:
  - `ghcr.io/example/payments-api:v1`
  - `ghcr.io/example/payments-api:v2`
  - `ghcr.io/example/incident-detector:latest`
- Slack webhook URL.

## 1) Bootstrap Namespace, RBAC, Observability, and Workloads

```bash
kubectl apply -f cluster/helm/base/namespace-rbac.yaml
./cluster/helm/install-observability.sh
kubectl apply -k apps/payments-api/v1
kubectl apply -f cluster/helm/detector/detector.yaml
kubectl -n demo-aiops create secret generic slack-webhook \
  --from-literal=webhook-url='https://hooks.slack.com/services/REAL/WEBHOOK' \
  -o yaml --dry-run=client | kubectl apply -f -
kubectl -n demo-aiops rollout status deploy/payments-api
kubectl -n demo-aiops rollout status deploy/incident-detector
```

## 2) Baseline Health (v1 healthy)

```bash
kubectl -n demo-aiops port-forward svc/payments-api 8080:80
curl -i http://127.0.0.1:8080/pay
# expect HTTP/1.1 200
```

## 3) Trigger Failure by Rolling Out v2

In a second terminal:

```bash
kubectl apply -k apps/payments-api/v2
kubectl -n demo-aiops rollout status deploy/payments-api
for i in {1..12}; do curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/pay; sleep 1; done
# expect repeated 500
```

## 4) Incident Detection + Slack Alert

```bash
kubectl -n demo-aiops port-forward svc/incident-detector 8081:8081
curl -s http://127.0.0.1:8081/incident | jq
```

Expected:
- `type: deployment regression`
- `severity: high`
- evidence includes `rollout_time`, `image_tag`, `error_count`, and `log_samples`
- Slack message appears with recommended rollback + `awaiting approval`

## 5) Human Approval

```bash
curl -s -X POST http://127.0.0.1:8081/approve \
  -H 'content-type: application/json' \
  -d '{"approver":"oncall-demo"}' | jq
```

## 6) Safe Rollback + Validation + Audit

Run from repo root with kube context set:

```bash
./runbooks/rollback-deployment/rollback.sh
cat runbooks/rollback-deployment/audit-record.json | jq
curl -i http://127.0.0.1:8080/pay
# expect HTTP/1.1 200 after rollback
```

## 7) Repeatability Run #2

Repeat steps 3 through 6 again:

```bash
kubectl apply -k apps/payments-api/v2
kubectl -n demo-aiops rollout status deploy/payments-api
for i in {1..12}; do curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/pay; sleep 1; done
curl -s http://127.0.0.1:8081/incident | jq
curl -s -X POST http://127.0.0.1:8081/approve -H 'content-type: application/json' -d '{"approver":"oncall-demo-2"}' | jq
./runbooks/rollback-deployment/rollback.sh
cat runbooks/rollback-deployment/audit-record.json | jq
```

If both runs complete with rollback validation and audit output, the MVP demo is successful.
