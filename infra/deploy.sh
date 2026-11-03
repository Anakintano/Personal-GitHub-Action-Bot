#!/usr/bin/env bash
set -euo pipefail

kubectl set image --local -f k8s/deployment.yaml "app=${1:?pass the pushed image}" -o yaml |
  kubectl apply -f -
kubectl -n ci-failure-explainer rollout status deployment/ci-failure-explainer --timeout=10m
