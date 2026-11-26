#!/usr/bin/env bash
# Resilience scenarios against the Kubernetes deployment, each run under load.
#
#   scripts/chaos.sh [pod-kill | rolling-restart | redis-outage | all]
#
# Each event is marked on the Grafana dashboard when GRAFANA_ADMIN_PASSWORD
# is set, and every scenario's status codes are appended to $RESULTS_FILE.
set -euo pipefail

NS=${NAMESPACE:-idempotency}
API=${API_URL:-http://localhost:8080}
GRAFANA=${GRAFANA_URL:-http://localhost:3000}
RESULTS=${RESULTS_FILE:-chaos-results.txt}

annotate() {
  [ -n "${GRAFANA_ADMIN_PASSWORD:-}" ] || return 0
  local code
  code=$(curl -s -o /tmp/annotation.json -w "%{http_code}" -u "admin:${GRAFANA_ADMIN_PASSWORD}" \
    -H 'Content-Type: application/json' \
    -d "{\"dashboardUID\":\"idempotency\",\"time\":$(date +%s%3N),\"text\":\"$1\",\"tags\":[\"chaos\"]}" \
    "$GRAFANA/api/annotations" || true)
  echo "annotation '$1': HTTP $code $(head -c 200 /tmp/annotation.json 2>/dev/null)" >> "${ANNOTATION_LOG:-annotations.log}"
}

start_load() {
  python scripts/load.py --base "$API" --seconds "$1" --concurrency 10 > "/tmp/load-$2.txt" &
  LOAD_PID=$!
}

finish_load() {
  wait "$LOAD_PID"
  echo "$1 | $(cat "/tmp/load-$2.txt")" | tee -a "$RESULTS"
}

pod_kill() {
  start_load 60 pod-kill
  sleep 20
  POD=$(kubectl -n "$NS" get pods -l app=api -o jsonpath='{.items[0].metadata.name}')
  annotate "API pod deleted"
  kubectl -n "$NS" delete pod "$POD" --wait=false
  kubectl -n "$NS" rollout status deployment/api --timeout=120s
  finish_load "Pod killed under load" pod-kill
}

rolling_restart() {
  start_load 60 rollout
  sleep 10
  annotate "Rolling restart"
  kubectl -n "$NS" rollout restart deployment/api
  kubectl -n "$NS" rollout status deployment/api --timeout=180s
  finish_load "Rolling restart under load" rollout
}

redis_outage() {
  start_load 75 redis
  sleep 20
  annotate "Redis down"
  kubectl -n "$NS" scale deployment/redis --replicas=0
  sleep 35
  kubectl -n "$NS" scale deployment/redis --replicas=1
  kubectl -n "$NS" rollout status deployment/redis --timeout=120s
  annotate "Redis back"
  finish_load "Redis outage under load" redis
}

case "${1:-all}" in
  pod-kill) pod_kill ;;
  rolling-restart) rolling_restart ;;
  redis-outage) redis_outage ;;
  all) pod_kill; sleep 10; rolling_restart; sleep 10; redis_outage ;;
  *) echo "unknown scenario: $1" >&2; exit 2 ;;
esac
