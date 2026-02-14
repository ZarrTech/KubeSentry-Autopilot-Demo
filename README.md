# KubeSentry Autopilot Demo

Demo-first MVP for detecting deployment regressions and safely remediating with rollback.

## Repository Layout

- `cluster/helm`: namespace/RBAC, observability values/install script, detector manifests
- `apps/payments-api`: sample service manifests and app code for `v1` + `v2`
- `services/incident-detector`: detector implementation (Python 3.11)
- `runbooks/rollback-deployment`: rollback script with validation + audit record
- `docs/demo-script.md`: end-to-end demo runbook
- `docs/architecture.md`: architecture and security model

## Quick Start

Follow `docs/demo-script.md`.
