#!/usr/bin/env bash
set -euo pipefail
export PATH="$PWD/.tools/bin:$PATH"
mkdir -p evidence .runtime
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
echo '=== Terraform saved plan and Ansible second-run idempotence ==='
ssh-keygen -q -t ed25519 -N '' -f .runtime/ansible-key
printf 'public_key_file = "%s"\n' "$PWD/.runtime/ansible-key.pub" > infra/local.tfvars
terraform -chdir=infra init -no-color
terraform -chdir=infra validate -no-color
terraform -chdir=infra plan -no-color -var-file=local.tfvars -out=../.runtime/create.tfplan
terraform -chdir=infra apply -no-color ../.runtime/create.tfplan
terraform -chdir=infra plan -no-color -var-file=local.tfvars -detailed-exitcode
sed "s|/ABSOLUTE/PATH/advanced-lab|$PWD|g" ansible/inventory.example.ini > ansible/inventory.ini
for port in 22220 22221; do
  ssh-keyscan -t ed25519 -p "$port" 127.0.0.1 >> .runtime/known_hosts
done
docker exec dva-node-a ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
ssh-keygen -lf .runtime/known_hosts
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml | tee evidence/ansible-first.txt
ansible-playbook -i ansible/inventory.ini ansible/playbook.yml | tee evidence/ansible-repeat.txt
grep -E 'node_a.*changed=0.*failed=0' evidence/ansible-repeat.txt
grep -E 'node_b.*changed=0.*failed=0' evidence/ansible-repeat.txt
curl -fsS http://127.0.0.1:22280/health
if ansible-playbook -i ansible/inventory.ini ansible/playbook.yml -e 'lab_message=bad!'; then exit 1; fi
echo '=== Kubernetes startup and actual useful HTTP ==='
./lab kube-start
curl -fsS -H 'Content-Type: application/json' -d '{"title":"old-kube-record"}' http://127.0.0.1:18230/tickets > evidence/old-kube.json
old=$(jq -r .id evidence/old-kube.json)
kubectl --context kind-dva-course -n stage delete pod postgres-0
kubectl --context kind-dva-course -n stage rollout status statefulset/postgres --timeout=180s
sleep 5
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/pvc-after.json
echo '=== Helm ownership, atomic failed update and old-data survival ==='
helm lint chart
helm template ticket-api chart -f chart/values-stage.yaml > evidence/rendered-stage.yaml
kubectl --context kind-dva-course -n stage delete deployment ticket-api service ticket-api
helm --kube-context kind-dva-course upgrade --install ticket-api chart -n stage -f chart/values-stage.yaml --atomic --wait --timeout 180s
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/helm-installed.json
if helm --kube-context kind-dva-course upgrade ticket-api chart -n stage -f chart/values-stage.yaml --set probes.readinessPath=/not-ready --atomic --wait --timeout 45s; then echo 'Bad readiness accepted'; exit 1; fi
./lab http --base http://127.0.0.1:18230 --read-id "$old" --expected evidence/old-kube.json --out evidence/helm-rollback.json
helm --kube-context kind-dva-course -n stage history ticket-api
echo '=== RBAC and minimal SQL permissions ==='
kubectl --context kind-dva-course apply -f security/rbac.yaml
kubectl --context kind-dva-course auth can-i get secrets --as system:serviceaccount:stage:ticket-runtime -n stage | grep '^no$'
echo '=== Backup, full SQL restore, HTTP comparison ==='
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=0
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=120s
./lab backup --namespace stage --other-writers-stopped
backup=$(find backups -mindepth 1 -maxdepth 1 -type d | sort | tail -1)
./lab restore "$backup" --destination restore-202610090001
kubectl --context kind-dva-course -n restore-202610090001 port-forward service/ticket-api 18400:8000 > evidence/port-forward.txt 2>&1 &
sleep 3
./lab http --base http://127.0.0.1:18400 --read-id "$old" --expected evidence/old-kube.json --out evidence/restored-http.json
kubectl --context kind-dva-course -n stage scale deployment ticket-api --replicas=2
kubectl --context kind-dva-course -n stage rollout status deployment/ticket-api --timeout=180s
echo '=== Small reproducible load using supplied profile ==='
./lab load --headless --host http://127.0.0.1:18220 -u 3 -r 1 -t 10s --csv evidence/load --only-summary
./lab analyze evidence/load_stats.csv > evidence/load-summary.json
echo 'ROUTE PASS: actual operations, negative cases and preserved old record'
