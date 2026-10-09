#!/usr/bin/env bash
set -euo pipefail
export PATH="$PWD/.tools/bin:$PATH"
mkdir -p evidence .runtime
./lab kube-start
kubectl --context kind-dva-course -n dev delete deployment/ticket-api service/ticket-api
helm --kube-context kind-dva-course upgrade --install ticket-api chart -n dev -f chart/values-dev.yaml --atomic --wait --timeout 180s
for n in $(seq 1 25); do
 curl -fsS -H 'Content-Type: application/json' -d "{\"title\":\"load-seed-$n\"}" http://127.0.0.1:18220/tickets > "evidence/seed-$n.json"
done
snapshot() {
 kubectl --context kind-dva-course -n dev exec postgres-0 -- psql -U postgres -d ticket_lab -Atc 'SELECT json_agg(t ORDER BY t.id)::text FROM (SELECT id,title,status FROM tickets) t;'
}
snapshot > evidence/dataset-before.json
test "$(jq 'length' evidence/dataset-before.json)" -gt 20
helm --kube-context kind-dva-course get values ticket-api -n dev --all > .runtime/m10-original.yaml
./lab load-read --headless --host http://127.0.0.1:18220 -u 5 -r 1 -t 5s --csv evidence/warmup --only-summary
for phase in a b; do
 limit=100m
 if [ "$phase" = b ]; then limit=500m; fi
 helm --kube-context kind-dva-course upgrade ticket-api chart -n dev -f .runtime/m10-original.yaml --set "resources.limits.cpu=$limit" --atomic --wait --timeout 180s
 kubectl --context kind-dva-course -n dev get deployment ticket-api -o json | jq -r '.spec.template.spec.containers[0].resources.limits.cpu' | grep -qx "$limit"
 for n in 1 2 3; do
  ./lab load-read --headless --host http://127.0.0.1:18220 -u 5 -r 1 -t 20s --csv "evidence/read-$phase$n" --only-summary
  ./lab analyze "evidence/read-$phase${n}_stats.csv" > "evidence/read-$phase$n.json"
  snapshot > "evidence/dataset-$phase$n.json"
  cmp evidence/dataset-before.json "evidence/dataset-$phase$n.json"
 done
done
helm --kube-context kind-dva-course upgrade ticket-api chart -n dev -f .runtime/m10-original.yaml --atomic --wait --timeout 180s
./lab http --base http://127.0.0.1:18220 --out evidence/load-returned-http.json
echo 'LOAD PASS: warmup plus 3+3 runs, one CPU factor, all database rows preserved, original configuration and HTTP restored'
