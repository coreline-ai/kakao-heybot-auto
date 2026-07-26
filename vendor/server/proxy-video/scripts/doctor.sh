#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v node >/dev/null
FFPROBE="${VIDEO_PROXY_FFPROBE_COMMAND:-/usr/bin/ffprobe}"
[[ "$FFPROBE" = /* && -x "$FFPROBE" ]]
[[ -s "$ROOT/runtime/secrets/manager.secret" ]]
[[ -s "$ROOT/runtime/secrets/grok-upstream.secret" ]]
curl --fail --silent --max-time 3 http://127.0.0.1:4358/ready >/dev/null
printf '%s\n' 'proxy-video dependencies ready'
