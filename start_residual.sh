#!/usr/bin/env bash
set -euo pipefail
TASK_ROOT="$(cd -- "$(dirname -- "$0")" && pwd)"
cd "$TASK_ROOT"
TASK_PORT=8766
if [[ "$#" -gt 0 ]]; then TASK_PORT="$1"; fi
case "$TASK_PORT" in ''|*[!0-9]*) echo "Port must be numeric."; exit 1;; esac
if ((TASK_PORT < 1024 || TASK_PORT > 65535)); then echo "Port must be 1024-65535."; exit 1; fi
TASK_PYTHON=""
for candidate in python3.13 python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c 'import sys; sys.exit(0 if (3,11) <= sys.version_info[:2] <= (3,13) else 1)' 2>/dev/null; then
    TASK_PYTHON="$candidate"; break
  fi
done
if [[ -z "$TASK_PYTHON" ]]; then echo "Install Python 3.11-3.13 from python.org or your system package manager."; exit 1; fi
export PYTHONPATH=""
export PYTHONNOUSERSITE=1
if [[ ! -x .venv/bin/python ]]; then
  echo "[1/3] Creating isolated .venv (Linux may require the python3-venv package)..."
  "$TASK_PYTHON" -m venv .venv
fi
TASK_HASH="$(.venv/bin/python -c 'import hashlib; print(hashlib.sha256(open("requirements.txt","rb").read()).hexdigest())')"
TASK_INSTALLED_HASH="$(cat .venv/residual-requirements.sha256 2>/dev/null || true)"
if [[ "$TASK_HASH" != "$TASK_INSTALLED_HASH" ]] || ! .venv/bin/python -c 'import pandas, sklearn, openpyxl, xlrd' 2>/dev/null; then
  echo "[2/3] Installing dependencies (internet required for initial setup)..."
  .venv/bin/python -m pip install --disable-pip-version-check -r requirements.txt
  printf '%s\n' "$TASK_HASH" > .venv/residual-requirements.sha256
fi
set +e
.venv/bin/python - "$TASK_PORT" <<'PY'
import json, socket, sys, urllib.request
port=int(sys.argv[1])
try:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health",timeout=2) as r:
        health=json.load(r)
    if health.get("app")=="residual" and health.get("version")=="0.4" and health.get("ok"):
        print(f"Residual is already running: http://127.0.0.1:{port}")
        sys.exit(10)
except (OSError,ValueError):
    pass
with socket.socket() as sock:
    if sock.connect_ex(("127.0.0.1",port))==0:
        print("Port occupied. Stop the other app or use: bash start_residual.sh 8767")
        sys.exit(1)
PY
TASK_STATUS=$?
set -e
if [[ "$TASK_STATUS" == 10 ]]; then exit 0; fi
if [[ "$TASK_STATUS" != 0 ]]; then exit "$TASK_STATUS"; fi
echo "[3/3] Open http://127.0.0.1:$TASK_PORT"
echo "Keep this terminal open. Press Ctrl+C to stop; saved data remains."
echo "For the model, install Ollama from ollama.com, run ollama serve, then ollama pull qwen3:1.7b."
exec .venv/bin/python -X utf8 -m src.serve --port "$TASK_PORT"
