#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v node >/dev/null
[[ -s "$ROOT/runtime/secrets/manager.secret" ]]
[[ -s "$ROOT/runtime/secrets/codex-upstream.secret" ]]
curl --fail --silent --max-time 3 http://127.0.0.1:4348/ready >/dev/null
printf '%s\n' "proxy-image dependencies ready"
