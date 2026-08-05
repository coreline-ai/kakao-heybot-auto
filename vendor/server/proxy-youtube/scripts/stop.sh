#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; pid_file="$ROOT/runtime/proxy-youtube.pid"
if [[ -s "$pid_file" ]]; then kill "$(<"$pid_file")" 2>/dev/null || true; rm -f "$pid_file"; fi
