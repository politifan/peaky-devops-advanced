#!/usr/bin/env bash
set -euo pipefail
export PATH="$PWD/.tools/bin:$PATH"
mkdir -p evidence .runtime
diagnostics() {
 code=$?
 if [ "$code" -ne 0 ]; then
  kubectl --context kind-dva-course get pods,jobs -A -o wide > evidence/failure-pods.txt 2>&1 || true
  kubectl --context kind-dva-course get events -A --sort-by=.metadata.creationTimestamp > evidence/failure-events.txt 2>&1 || true
  docker exec dva-course-control-plane cat /etc/containerd/config.toml > evidence/failure-containerd.txt 2>&1 || true
 fi
}
trap diagnostics EXIT
echo '=== Foundation: two instances and distinct environments ==='
./lab foundation-start stage
./lab foundation-start dev
./lab observe --a http://127.0.0.1:18210 --b http://127.0.0.1:18211 --write-cycle --label stage --out evidence/foundation-stage.json
./lab observe --a http://127.0.0.1:18200 --b http://127.0.0.1:18201 --write-cycle --label dev --out evidence/foundation-dev.json
curl -fsS -H 'Content-Type: application/json' -d '{"title":"must-survive"}' http://127.0.0.1:18210/tickets > evidence/foundation-old.json
old=$(jq -r .id evidence/foundation-old.json)
docker compose -f foundation/compose.yaml --project-directory foundation -p dva-stage --env-file foundation/env.stage stop api_a
if curl --max-time 3 -fsS http://127.0.0.1:18210/ready; then echo 'Unexpected available stopped instance'; exit 1; fi
curl -fsS "http://127.0.0.1:18211/tickets/$old" | jq -S . > evidence/foundation-after.json
jq -S . evidence/foundation-old.json > evidence/foundation-before.json
diff evidence/foundation-before.json evidence/foundation-after.json
docker compose -f foundation/compose.yaml --project-directory foundation -p dva-stage --env-file foundation/env.stage start api_a
# Quiesce only this course's Compose services; persistent volumes stay present.
docker compose -f foundation/compose.yaml --project-directory foundation -p dva-stage --env-file foundation/env.stage stop
docker compose -f foundation/compose.yaml --project-directory foundation -p dva-dev --env-file foundation/env.dev stop
echo '=== Terraform saved plan and Ansible second-run idempotence ==='
ssh-keygen -q -t ed25519 -N '' -f .runtime/ansible-key
printf 'public_key_file = "%s"\n' "$PWD/.runtime/ansible-key.pub" > infra/local.tfvars
terraform -chdir=infra init -no-color
terraform -chdir=infra validate -no-color
terraform -chdir=infra plan -no-color -var-file=local.tfvars -out=../.runtime/create.tfplan
terraform -chdir=infra apply -no-color ../.runtime/create.tfplan
terraform -chdir=infra plan -no-color -var-file=local.tfvars -detailed-exitcode

echo '=== Terraform drift, import and persistent marker ==='
docker exec dva-node-a sh -c 'printf "must-survive\n" > /srv/dva/student-marker.txt'
docker stop dva-node-a
terraform -chdir=infra plan -no-color -var-file=local.tfvars -out=../.runtime/repair.tfplan
terraform -chdir=infra apply -no-color ../.runtime/repair.tfplan
docker exec dva-node-a cat /srv/dva/student-marker.txt | grep '^must-survive$'
docker network create --label course=dva dva-import-lab
cat > infra/import-lab.tf <<'HCL'
resource "docker_network" "imported" {
  name = "dva-import-lab"
  labels {
    label = "course"
    value = "dva"
  }
}
HCL
terraform -chdir=infra import -no-color -var-file=local.tfvars docker_network.imported dva-import-lab
terraform -chdir=infra plan -no-color -var-file=local.tfvars -detailed-exitcode
sed -i 's/ssh_port = 22220/ssh_port = 22222/' infra/main.tf
terraform -chdir=infra plan -no-color -var-file=local.tfvars -out=../.runtime/port.tfplan
terraform -chdir=infra apply -no-color ../.runtime/port.tfplan
docker exec dva-node-a cat /srv/dva/student-marker.txt | grep '^must-survive$'
sed -i 's/ssh_port = 22222/ssh_port = 22220/' infra/main.tf
terraform -chdir=infra plan -no-color -var-file=local.tfvars -out=../.runtime/port-return.tfplan
terraform -chdir=infra apply -no-color ../.runtime/port-return.tfplan

sed "s|/ABSOLUTE/PATH/advanced-lab|$PWD|g" ansible/inventory.example.ini > ansible/inventory.ini
./lab ssh-trust
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml | tee evidence/ansible-first.txt
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml | tee evidence/ansible-repeat.txt
grep -E 'node_a.*changed=0.*failed=0' evidence/ansible-repeat.txt
grep -E 'node_b.*changed=0.*failed=0' evidence/ansible-repeat.txt
curl -fsS http://127.0.0.1:22280/health
if ansible-playbook -i ansible/inventory.ini ansible/playbook.yml -e 'lab_message=bad!'; then exit 1; fi
echo '=== Kubernetes startup and actual useful HTTP ==='
./lab kube-start
kubectl --context kind-dva-course -n stage get deployment,service,pvc
./lab guard
curl -fsS -H 'Content-Type: application/json' -d '{"title":"old-kube-record"}' http://127.0.0.1:18230/tickets > evidence/old-kube.json
old=$(jq -r .id evidence/old-kube.json)
kubectl --context kind-dva-course -n stage delete pod postgres-0
kubectl --context kind-dva-course -n stage rollout status statefulset/postgres --timeout=180s
sleep 5
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/pvc-after.json
echo '=== Helm ownership, atomic failed update and old-data survival ==='
helm lint chart
helm template ticket-api chart -f chart/values-stage.yaml > evidence/rendered-stage.yaml
kubectl --context kind-dva-course -n stage delete deployment/ticket-api service/ticket-api
helm --kube-context kind-dva-course upgrade --install ticket-api chart -n stage -f chart/values-stage.yaml --atomic --wait --timeout 180s
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/helm-installed.json
if helm --kube-context kind-dva-course upgrade ticket-api chart -n stage -f chart/values-stage.yaml --set probes.readinessPath=/not-ready --atomic --wait --timeout 45s; then echo 'Bad readiness accepted'; exit 1; fi
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/helm-rollback.json
helm --kube-context kind-dva-course -n stage history ticket-api
echo '=== RBAC and minimal SQL permissions ==='
kubectl --context kind-dva-course apply -f security/stage-reader.yaml
actual=$(kubectl --context kind-dva-course auth can-i get pods -n stage --as=system:serviceaccount:stage:ticket-reader)
test "$actual" = yes
actual=$(kubectl --context kind-dva-course auth can-i get secrets -n dev --as=system:serviceaccount:stage:ticket-reader || true)
test "$actual" = no
echo 'Reader positive=yes; cross-namespace Secrets=no'
kubectl --context kind-dva-course -n stage get pods --as=system:serviceaccount:stage:ticket-reader
if kubectl --context kind-dva-course -n dev get pods --as=system:serviceaccount:stage:ticket-reader; then exit 1; fi
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=0
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=120s
./lab db-role create --namespace stage
./lab db-role rotate --namespace stage
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=2
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=180s
kubectl --context kind-dva-course -n stage exec postgres-0 -- psql -U postgres -d ticket_lab -v ON_ERROR_STOP=1 -c 'REVOKE INSERT ON TABLE tickets FROM ticket_runtime;'
curl -fsS http://127.0.0.1:18230/ready
if ./lab http --base http://127.0.0.1:18230 --out evidence/insert-forbidden.json; then echo 'Forbidden INSERT succeeded'; exit 1; fi
kubectl --context kind-dva-course -n stage exec postgres-0 -- psql -U postgres -d ticket_lab -v ON_ERROR_STOP=1 -c 'GRANT INSERT ON TABLE tickets TO ticket_runtime;'
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/permissions-returned.json
if kubectl --context kind-dva-course -n stage exec postgres-0 -- psql -U postgres -d ticket_lab -v ON_ERROR_STOP=1 -c 'BEGIN; SET LOCAL ROLE ticket_runtime; DELETE FROM tickets WHERE false; ROLLBACK;'; then echo 'Forbidden DELETE allowed'; exit 1; fi

echo '=== Prometheus rules, pending, firing and resolution ==='
./lab monitoring-admin
kubectl --context kind-dva-course apply -f monitoring/rbac.json
kubectl --context kind-dva-course apply -f monitoring/stack.json
kubectl --context kind-dva-course -n monitoring rollout status deployment/prometheus --timeout=180s
kubectl --context kind-dva-course -n monitoring rollout status deployment/grafana --timeout=180s
kubectl --context kind-dva-course -n monitoring exec deployment/prometheus -- promtool check config /etc/prometheus/prometheus.yml
kubectl --context kind-dva-course -n monitoring exec deployment/prometheus -- promtool check rules /etc/prometheus/rules.yml
kubectl --context kind-dva-course -n monitoring port-forward service/prometheus 19090:9090 > evidence/prometheus-port.txt 2>&1 &
sleep 3
curl -fsS http://127.0.0.1:19090/api/v1/targets > evidence/targets.json
kubectl --context kind-dva-course -n stage scale statefulset/postgres --replicas=0
pending=0;firing=0
for attempt in $(seq 1 50); do
 curl -fsS http://127.0.0.1:19090/api/v1/alerts > evidence/alerts-current.json
 if jq -e '.data.alerts[] | select(.labels.alertname=="TicketReadinessLost" and .labels.namespace=="stage" and .state=="pending")' evidence/alerts-current.json >/dev/null; then pending=1; cp evidence/alerts-current.json evidence/alerts-pending.json; fi
 if jq -e '.data.alerts[] | select(.labels.alertname=="TicketReadinessLost" and .labels.namespace=="stage" and .state=="firing")' evidence/alerts-current.json >/dev/null; then firing=1;cp evidence/alerts-current.json evidence/alerts-firing.json;break;fi
 sleep 2
done
test "$pending" = 1;test "$firing" = 1
.venv-ui/bin/python qa/capture_ui.py alert
kubectl --context kind-dva-course -n stage scale statefulset/postgres --replicas=1
kubectl --context kind-dva-course -n stage rollout status statefulset/postgres --timeout=180s
for attempt in $(seq 1 45); do
 curl -fsS http://127.0.0.1:19090/api/v1/alerts > evidence/alerts-resolved.json
 if ! jq -e '.data.alerts[] | select(.labels.alertname=="TicketReadinessLost" and .labels.namespace=="stage")' evidence/alerts-resolved.json >/dev/null; then break;fi
 sleep 2
done
! jq -e '.data.alerts[] | select(.labels.alertname=="TicketReadinessLost" and .labels.namespace=="stage")' evidence/alerts-resolved.json >/dev/null
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/alert-return-http.json
echo 'Alert verified: pending -> firing -> resolved; old data preserved'
kubectl --context kind-dva-course -n monitoring port-forward service/grafana 13000:3000 > evidence/grafana-port.txt 2>&1 &
sleep 3
.venv-ui/bin/python qa/capture_ui.py dashboard

echo '=== Backup, full SQL restore, HTTP comparison ==='
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=0
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=120s
./lab backup --namespace stage --other-writers-stopped
backup=$(find backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1)
./lab restore "$backup" --destination restore-20261009000100
kubectl --context kind-dva-course -n restore-20261009000100 port-forward service/ticket-api 18400:8000 > evidence/port-forward.txt 2>&1 &
sleep 3
./lab http --base http://127.0.0.1:18400 --read-id "$old" --expected evidence/old-kube.json --out evidence/restored-http.json
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=2
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=180s

echo '=== One CI-checked registry digest promoted dev then stage ==='
kubectl --context kind-dva-course -n dev delete deployment/ticket-api service/ticket-api
helm --kube-context kind-dva-course upgrade --install ticket-api chart -n dev -f chart/values-dev.yaml --atomic --wait --timeout 180s
./lab build-check
./lab registry
# Test the node's actual CRI path, not only the host's successful push.
./lab deploy --artifact evidence --commit "$GITHUB_SHA"
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/promoted-old-http.json

echo '=== Small reproducible load using supplied profile ==='
./lab load --headless --host http://127.0.0.1:18220 -u 3 -r 1 -t 10s --csv evidence/load --only-summary
./lab analyze evidence/load_stats.csv > evidence/load-summary.json
before=$(curl -fsS http://127.0.0.1:18220/tickets | jq 'length')
./lab load-read --headless --host http://127.0.0.1:18220 -u 3 -r 1 -t 10s --csv evidence/read-load --only-summary
./lab analyze evidence/read-load_stats.csv > evidence/read-load-summary.json
after=$(curl -fsS http://127.0.0.1:18220/tickets | jq 'length')
test "$before" = "$after"
echo "Read-only load preserved $before records"
echo 'ROUTE PASS: actual operations, negative cases and preserved old record'
