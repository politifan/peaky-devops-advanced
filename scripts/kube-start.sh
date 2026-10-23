#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$PWD/.tools/bin:$PATH"
if kind get clusters | grep -qx dva-course; then echo 'Кластер уже существует: сначала осмотрите его и привязку'; exit 1; fi
kind create cluster --name dva-course --config k8s/kind.yaml --wait 120s
./lab guard --bind
kubectl --context kind-dva-course apply -f k8s/namespaces.yaml
docker build -t dva-ticket:advanced-m04 ticket-lab
kind load docker-image dva-ticket:advanced-m04 --name dva-course
for ns in dev stage; do
  ./lab secrets "$ns"
  kubectl --context kind-dva-course -n "$ns" apply -f k8s/db.yaml
  kubectl --context kind-dva-course -n "$ns" rollout status statefulset/postgres --timeout=180s
  kubectl --context kind-dva-course -n "$ns" apply -f k8s/init-job.yaml
  kubectl --context kind-dva-course -n "$ns" wait --for=condition=complete job/init-db-m04 --timeout=180s
  kubectl --context kind-dva-course -n "$ns" apply -f k8s/migrate-job.yaml
  kubectl --context kind-dva-course -n "$ns" wait --for=condition=complete job/migrate-m04 --timeout=180s
  kubectl --context kind-dva-course -n "$ns" apply -f k8s/app.yaml
  kubectl --context kind-dva-course -n "$ns" rollout status deployment/ticket-api --timeout=180s
done
kubectl --context kind-dva-course -n dev patch service ticket-api --type merge -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":8000,"targetPort":"http","nodePort":30220}]}}'
kubectl --context kind-dva-course -n stage patch service ticket-api --type merge -p '{"spec":{"type":"NodePort","ports":[{"name":"http","port":8000,"targetPort":"http","nodePort":30230}]}}'
mkdir -p evidence
for port in 18220 18230; do
  ready=0
  for attempt in $(seq 1 60); do
    if curl -fsS --max-time 3 "http://127.0.0.1:$port/ready" >/dev/null; then ready=1; break; fi
    sleep 1
  done
  test "$ready" = 1 || { echo "NodePort $port not ready"; exit 1; }
done
./lab http --base http://127.0.0.1:18220 --out "evidence/dev-$(date +%s).json"
./lab http --base http://127.0.0.1:18230 --out "evidence/stage-$(date +%s).json"
