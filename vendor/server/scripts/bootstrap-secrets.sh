#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FORCE=false
if [[ "${1:-}" == "--force" ]]; then
  FORCE=true
elif [[ -n "${1:-}" ]]; then
  printf '%s\n' "usage: bootstrap-secrets.sh [--force]" >&2
  exit 2
fi

secret() {
  openssl rand -hex 32
}

write_pair() {
  local first="$1"
  local second="$2"
  if [[ "$FORCE" == "false" && -s "$first" && -s "$second" ]]; then
    chmod 600 "$first" "$second"
    return
  fi
  local value
  value="$(secret)"
  mkdir -p "$(dirname "$first")" "$(dirname "$second")"
  umask 077
  printf '%s\n' "$value" >"$first"
  printf '%s\n' "$value" >"$second"
  chmod 600 "$first" "$second"
}

for package in proxy-manager proxy-image proxy-vision proxy-codex proxy-video proxy-grok proxy-draw proxy-brush proxy-conversation; do
  mkdir -p "$ROOT/$package/runtime/secrets"
  chmod 700 "$ROOT/$package/runtime" "$ROOT/$package/runtime/secrets"
done
mkdir -p "$ROOT/proxy-codex/runtime/secrets/callers"
chmod 700 "$ROOT/proxy-codex/runtime/secrets/callers"

umask 077
if [[ "$FORCE" == "true" || ! -s "$ROOT/proxy-manager/runtime/secrets/route.secret" ]]; then
  secret >"$ROOT/proxy-manager/runtime/secrets/route.secret"
fi
if [[ "$FORCE" == "true" || ! -s "$ROOT/proxy-manager/runtime/secrets/admin.secret" ]]; then
  secret >"$ROOT/proxy-manager/runtime/secrets/admin.secret"
fi
chmod 600 \
  "$ROOT/proxy-manager/runtime/secrets/route.secret" \
  "$ROOT/proxy-manager/runtime/secrets/admin.secret"

write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-image.secret" \
  "$ROOT/proxy-image/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-codex.secret" \
  "$ROOT/proxy-codex/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-video.secret" \
  "$ROOT/proxy-video/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-video/runtime/secrets/grok-upstream.secret" \
  "$ROOT/proxy-grok/runtime/secrets/video-upstream.secret"
write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-draw.secret" \
  "$ROOT/proxy-draw/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-image/runtime/secrets/codex-upstream.secret" \
  "$ROOT/proxy-codex/runtime/secrets/callers/image.secret"
write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-vision.secret" \
  "$ROOT/proxy-vision/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-vision/runtime/secrets/codex-upstream.secret" \
  "$ROOT/proxy-codex/runtime/secrets/callers/vision.secret"
write_pair \
  "$ROOT/proxy-draw/runtime/secrets/codex-upstream.secret" \
  "$ROOT/proxy-codex/runtime/secrets/callers/draw.secret"
write_pair \
  "$ROOT/proxy-draw/runtime/secrets/brush-upstream.secret" \
  "$ROOT/proxy-brush/runtime/secrets/draw-upstream.secret"
write_pair \
  "$ROOT/proxy-manager/runtime/secrets/proxy-conversation.secret" \
  "$ROOT/proxy-conversation/runtime/secrets/manager.secret"
write_pair \
  "$ROOT/proxy-conversation/runtime/secrets/codex-conversation.secret" \
  "$ROOT/proxy-codex/runtime/secrets/callers/conversation.secret"
write_pair \
  "$ROOT/proxy-conversation/runtime/secrets/grok-conversation.secret" \
  "$ROOT/proxy-grok/runtime/secrets/grok-conversation.secret"

# These secrets are used by the manager's authenticated health contract.  The
# internal grok/brush readiness endpoints are deliberately unauthenticated but
# the manager still requires a private credential file for every enabled entry.
for secret_file in \
  "$ROOT/proxy-manager/runtime/secrets/proxy-grok.secret" \
  "$ROOT/proxy-manager/runtime/secrets/proxy-brush.secret"; do
  if [[ "$FORCE" == "true" || ! -s "$secret_file" ]]; then
    secret >"$secret_file"
  fi
  chmod 600 "$secret_file"
done

printf '%s\n' "Proxy secrets are present with separate route/admin/internal roles."
