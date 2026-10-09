#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -x .venv-load/bin/locust || { python3 -m venv .venv-load; .venv-load/bin/pip install -r load/requirements.txt; }
exec .venv-load/bin/locust -f load/locustfile.py "$@"
