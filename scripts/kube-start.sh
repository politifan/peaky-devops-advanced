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
./lab http --base http://127.0.0.1:18220 --out "evidence/dev-$(date +%s).json"
./lab http --base http://127.0.0.1:18230 --out "evidence/stage-$(date +%s).json"
