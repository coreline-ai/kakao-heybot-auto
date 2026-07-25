#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$ROOT/runtime/state/server.pid"
if [[ -s "$PID_FILE" ]]; then
  pid="$(<"$PID_FILE")"
  kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..50}; do kill -0 "$pid" 2>/dev/null || break; sleep 0.1; done
  : >"$PID_FILE"
fi
