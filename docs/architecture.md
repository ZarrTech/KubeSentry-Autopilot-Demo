# KubeSentry Autopilot Demo Architecture

## Scope
This MVP demonstrates autonomous incident detection and controlled rollback for `payments-api` in a single namespace (`demo-aiops`) without cluster-admin privileges.

## Components

1. **payments-api** (`apps/payments-api`)
   - `v1`: `/pay` returns HTTP 200.
   - `v2`: `/pay` returns HTTP 500 when `PAYMENTS_GATEWAY_TOKEN` is absent.
   - Structured JSON logs include `version`, `status_code`, and `message`.

2. **Observability** (`cluster/helm`)
   - `kube-prometheus-stack` for metrics/events scraping.
   - Loki + Promtail for log aggregation.
   - Promtail scrape restricted to namespace `demo-aiops`.

3. **Incident Detector** (`services/incident-detector` + `cluster/helm/detector`)
   - Runs in-cluster as `aiops-detector-sa`.
   - Watches Deployment/ReplicaSet rollout metadata.
   - Queries Loki for repeated 500 logs after rollout.
   - Rule: repeated 500s within 10 minutes after rollout => `deployment regression` incident.
   - Generates evidence-rich incident JSON and sends Slack alert.
   - Exposes `/approve` for human-in-the-loop approval.

4. **Rollback Runbook** (`runbooks/rollback-deployment/rollback.sh`)
   - Intended to run under `aiops-remediator-sa` scoped to `demo-aiops`.
   - Preconditions:
     - incident approved
     - previous ReplicaSet exists
     - rollout age within policy window
   - Action: `kubectl rollout undo deployment/payments-api -n demo-aiops`.
   - Validation:
     - `/pay` returns 200
     - 5xx logs reduced/checked post rollback
   - Writes local JSON audit trail.

## Security Model (Least Privilege)

- `aiops-detector-sa`: list/watch/get `events`, `pods`, `deployments`, `replicasets` in `demo-aiops` only.
- `aiops-remediator-sa`: limited to deployment patch/get and ReplicaSet/pod read in `demo-aiops`.
- No secret read permission granted to either service account.
- No `exec`, no cluster roles, no cluster-admin bindings.

## Incident Lifecycle

1. Rollout to new image tag is observed.
2. Detector queries Loki for post-rollout 500 signatures.
3. Detector creates incident JSON evidence and sends Slack alert (`severity=high`, recommended rollback, awaiting approval).
4. Operator approves incident via detector endpoint.
5. Runbook executes rollback and validates recovery.
6. Audit JSON is written for traceability.

