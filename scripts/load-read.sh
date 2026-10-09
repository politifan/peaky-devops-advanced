#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
test -x .venv-load/bin/locust || bash scripts/load.sh --version
exec .venv-load/bin/locust -f load/read_only.py "$@"
