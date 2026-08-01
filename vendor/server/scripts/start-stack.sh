#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

[[ -s "$ROOT/proxy-manager/runtime/secrets/route.secret" ]] ||
  "$ROOT/scripts/bootstrap-secrets.sh"

for package in proxy-codex proxy-image proxy-vision proxy-manager proxy-conversation; do
  (cd "$ROOT/$package" && npm run build >/dev/null)
done

"$ROOT/proxy-codex/scripts/start.sh"
"$ROOT/proxy-image/scripts/start.sh"
"$ROOT/proxy-vision/scripts/start.sh"
"$ROOT/proxy-manager/scripts/start.sh"
"$ROOT/proxy-conversation/scripts/start.sh"

for _ in {1..30}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:4340/ready >/dev/null; then
    printf '%s\n' "HeyBot proxy stack is ready."
    exit 0
  fi
  sleep 1
done

printf '%s\n' "Proxy stack did not become ready." >&2
exit 1
