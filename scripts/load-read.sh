#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 load/target_safety.py "$@"
./lab guard
test -x .venv-load/bin/locust || bash scripts/load.sh --host http://127.0.0.1:18220 --version
export PYTHONPATH="$PWD/load${PYTHONPATH:+:$PYTHONPATH}"
exec .venv-load/bin/locust -f load/read_only.py "$@" --web-host 127.0.0.1

