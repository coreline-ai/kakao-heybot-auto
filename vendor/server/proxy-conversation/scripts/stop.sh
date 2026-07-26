#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -s "$ROOT/runtime/proxy-conversation.pid" ]]; then
  kill "$(<"$ROOT/runtime/proxy-conversation.pid")" 2>/dev/null || true
  rm -f "$ROOT/runtime/proxy-conversation.pid"
fi
