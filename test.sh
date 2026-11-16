#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
if [[ ! -x .venv/bin/python ]]; then
  "${PYTHON_BIN:-python3}" -m venv .venv
fi
.venv/bin/python -m pip install -r requirements-dev.txt
exec .venv/bin/python -m pytest -q "$@"
