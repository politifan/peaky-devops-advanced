#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
python3 load/target_safety.py "$@"
./lab guard
test -x .venv-load/bin/locust || { python3 -m venv .venv-load; .venv-load/bin/pip install -r load/requirements.txt; }
export PYTHONPATH="$PWD/load${PYTHONPATH:+:$PYTHONPATH}"
exec .venv-load/bin/locust -f load/locustfile.py "$@" --web-host 127.0.0.1

