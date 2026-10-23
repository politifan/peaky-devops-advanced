#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
envname=${1:?Укажите dev или stage}
case "$envname" in dev|stage);; *) exit 2;; esac
python3 ops/foundation_prepare.py "$envname"
docker build -t dva-ticket:baseline ticket-lab
dc=(docker compose -p "dva-$envname" --env-file "foundation/env.$envname" -f foundation/compose.yaml)
"${dc[@]}" config --quiet
"${dc[@]}" up -d --wait --wait-timeout 120 db
"${dc[@]}" run --rm init
"${dc[@]}" run --rm migrate
"${dc[@]}" up -d --wait --wait-timeout 120 api_a api_b
echo 'Foundation ready; ports: dev 18200/18201, stage 18210/18211'
