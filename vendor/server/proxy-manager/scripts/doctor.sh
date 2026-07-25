#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v node >/dev/null
[[ -s "$ROOT/runtime/secrets/route.secret" ]]
[[ -s "$ROOT/runtime/secrets/admin.secret" ]]
[[ -s "$ROOT/config/proxies.json" ]]
printf '%s\n' "proxy-manager configuration present"
