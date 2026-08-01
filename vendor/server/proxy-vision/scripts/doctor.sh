#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
test -s "$ROOT/runtime/secrets/manager.secret"
test -s "$ROOT/runtime/secrets/codex-upstream.secret"
(cd "$ROOT" && npm run build >/dev/null)
printf '%s\n' 'proxy-vision doctor: OK'
