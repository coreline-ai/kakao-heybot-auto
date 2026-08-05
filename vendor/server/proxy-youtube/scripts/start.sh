#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
pid_file="$ROOT/runtime/proxy-youtube.pid"
if [[ -s "$pid_file" ]] && kill -0 "$(<"$pid_file")" 2>/dev/null; then exit 0; fi
(cd "$ROOT" && nohup npm start >runtime/launch.log 2>runtime/launch-error.log & echo $! >"$pid_file")
