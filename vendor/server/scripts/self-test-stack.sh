#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROUTE_SECRET_FILE="$ROOT/proxy-manager/runtime/secrets/route.secret"
ADMIN_SECRET_FILE="$ROOT/proxy-manager/runtime/secrets/admin.secret"

[[ -s "$ROUTE_SECRET_FILE" && -s "$ADMIN_SECRET_FILE" ]] ||
  { printf '%s\n' "Run scripts/bootstrap-secrets.sh first." >&2; exit 1; }

curl --fail --silent http://127.0.0.1:4340/health >/dev/null
curl --fail --silent http://127.0.0.1:4340/ready >/dev/null
"$ROOT/proxy-codex/scripts/self-test.sh" >/dev/null
"$ROOT/proxy-image/scripts/self-test.sh" >/dev/null
"$ROOT/proxy-manager/scripts/self-test.sh" >/dev/null
curl --fail --silent \
  -H "Authorization: Bearer $(<"$ADMIN_SECRET_FILE")" \
  http://127.0.0.1:4340/manager/v1/proxies >/dev/null

printf '%s\n' "Health, readiness, and admin registry checks passed."
