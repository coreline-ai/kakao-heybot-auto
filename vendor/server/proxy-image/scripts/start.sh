#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/runtime/logs" "$ROOT/runtime/state"
PID_FILE="$ROOT/runtime/state/server.pid"
if [[ -s "$PID_FILE" ]] && kill -0 "$(<"$PID_FILE")" 2>/dev/null; then exit 0; fi
(
  cd "$ROOT"
  nohup node dist/src/index.js >>runtime/logs/server.log 2>&1 &
  echo $! >"$PID_FILE"
)
