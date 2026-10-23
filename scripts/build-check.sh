#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ -n "${GITHUB_ACTIONS:-}" ]]; then exec python ci/build.py; fi
test -x .venv-ci/bin/python || python3 -m venv .venv-ci
export PATH="$PWD/.venv-ci/bin:$PATH"
export GITHUB_SHA="$(git rev-parse HEAD)"
exec python ci/build.py
