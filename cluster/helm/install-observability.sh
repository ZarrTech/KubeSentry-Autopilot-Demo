#!/usr/bin/env bash
set -euo pipefail

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add grafana https://grafana.github.io/helm-charts
helm repo update

kubectl create namespace monitoring --dry-run=client -o yaml | kubectl apply -f -

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  -f cluster/helm/prometheus/values.yaml

helm upgrade --install loki grafana/loki \
  --namespace monitoring \
  -f cluster/helm/loki/values.yaml

helm upgrade --install promtail grafana/promtail \
  --namespace monitoring \
  -f cluster/helm/loki/promtail-values.yaml
