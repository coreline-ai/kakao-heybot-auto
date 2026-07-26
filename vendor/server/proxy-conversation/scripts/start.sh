#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/runtime/logs" "$ROOT/runtime/secrets"
if [[ -f "$ROOT/runtime/proxy-conversation.pid" ]] && kill -0 "$(<"$ROOT/runtime/proxy-conversation.pid")" 2>/dev/null; then
  exit 0
fi
nohup node "$ROOT/dist/src/index.js" >>"$ROOT/runtime/logs/launchd.log" 2>>"$ROOT/runtime/logs/launchd-error.log" &
printf '%s\n' "$!" >"$ROOT/runtime/proxy-conversation.pid"
