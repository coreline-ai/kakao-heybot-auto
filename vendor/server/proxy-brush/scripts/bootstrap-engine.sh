#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="${PEN_BRUSH_PROXY_BOOTSTRAP_PYTHON:-python3.11}"
npm ci --prefix "$ROOT/engine"
node "$ROOT/scripts/ensure-remotion-browser.mjs"
if [ ! -x "$ROOT/runtime/python-venv/bin/python" ]; then
  "$PYTHON" -m venv "$ROOT/runtime/python-venv"
fi
"$ROOT/runtime/python-venv/bin/python" -m pip install --constraint "$ROOT/requirements-core.lock" setuptools==79.0.1
"$ROOT/runtime/python-venv/bin/python" -m pip install --no-build-isolation --constraint "$ROOT/requirements-core.lock" -e "$ROOT/engine/pipeline[dev]"
