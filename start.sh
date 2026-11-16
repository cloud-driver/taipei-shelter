#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ ! -x .venv/bin/python ]]; then
  "$PYTHON_BIN" -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, pydantic, PIL, starlette; from importlib.metadata import version; assert fastapi.__version__ == "0.136.1" and uvicorn.__version__ == "0.46.0" and pydantic.__version__ == "2.13.4" and PIL.__version__ == "12.3.0" and starlette.__version__ == "1.0.0" and version("anyio") == "4.13.0"' 2>/dev/null; then
  .venv/bin/python -m pip install -r requirements.txt
fi
exec .venv/bin/python -m uvicorn app.main:app_factory --factory --host "${SHELTER_HOST:-127.0.0.1}" --port "${PORT:-8765}" --no-access-log
