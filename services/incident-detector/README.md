# Incident Detector Service

Python 3.11 service that correlates recent `payments-api` rollouts with repeated 500s from Loki.

## Responsibilities
- Watches `payments-api` Deployment + ReplicaSets in `demo-aiops`.
- Detects repeated 500s in the 10-minute post-rollout window.
- Emits incident JSON evidence to `/var/run/aiops`.
- Sends a Slack incoming-webhook alert with severity + rollback recommendation.
- Exposes approval endpoint (`POST /approve`) for manual human approval.

## Build
```bash
docker build -t ghcr.io/example/incident-detector:latest services/incident-detector
```
