#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
test -x "$ROOT/runtime/python-venv/bin/python"
test -d "$ROOT/engine/node_modules/@remotion/cli"
test -f "$ROOT/runtime/remotion-browser/browser.json"
command -v ffmpeg >/dev/null
command -v ffprobe >/dev/null
printf 'proxy-brush runtime ready\n'
