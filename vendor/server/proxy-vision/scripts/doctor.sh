#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test -s "$ROOT/runtime/secrets/manager.secret"
test -s "$ROOT/runtime/secrets/codex-upstream.secret"
FFMPEG_COMMAND="${VISION_PROXY_FFMPEG_COMMAND:-$(command -v ffmpeg)}"
test -x "$FFMPEG_COMMAND"
(cd "$ROOT" && npm run build >/dev/null)
printf '%s\n' 'proxy-vision doctor: OK'
