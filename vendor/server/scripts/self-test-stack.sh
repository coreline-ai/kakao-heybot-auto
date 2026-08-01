#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_ROOT="$(cd "$ROOT/../.." && pwd)"
ROUTE_SECRET_FILE="$ROOT/proxy-manager/runtime/secrets/route.secret"
ADMIN_SECRET_FILE="$ROOT/proxy-manager/runtime/secrets/admin.secret"

[[ -s "$ROUTE_SECRET_FILE" && -s "$ADMIN_SECRET_FILE" ]] ||
  { printf '%s\n' "Run scripts/bootstrap-secrets.sh first." >&2; exit 1; }

curl --fail --silent http://127.0.0.1:4340/health >/dev/null
curl --fail --silent http://127.0.0.1:4340/ready >/dev/null
curl --fail --silent http://127.0.0.1:4361/ready >/dev/null

# Keep the complete provider/gateway regression set behind one entry point.  The
# packages do not share a test process, so a failure identifies the exact
# package while preserving the existing live-stack checks above.
for package in \
  proxy-manager \
  proxy-image \
  proxy-vision \
  proxy-video \
  proxy-draw \
  proxy-brush \
  proxy-codex \
  proxy-grok \
  proxy-conversation; do
  printf 'Testing %s...\n' "$package"
  (cd "$ROOT/$package" && npm test)
done

curl --fail --silent \
  -H "Authorization: Bearer $(<"$ADMIN_SECRET_FILE")" \
  http://127.0.0.1:4340/manager/v1/proxies >/dev/null

if [[ "${RUN_ANDROID_BUILD:-true}" == "true" ]]; then
  ANDROID_ROOT="$PROJECT_ROOT/vendor/android"
  [[ -x "$ANDROID_ROOT/gradlew" ]] || { printf '%s\n' "Iris gradlew not found." >&2; exit 1; }
  if [[ -n "${JAVA_HOME:-}" && ! -x "$JAVA_HOME/bin/java" ]]; then
    unset JAVA_HOME
  fi
  if [[ -z "${JAVA_HOME:-}" && -x "/Applications/Android Studio.app/Contents/jbr/Contents/Home/bin/java" ]]; then
    export JAVA_HOME="/Applications/Android Studio.app/Contents/jbr/Contents/Home"
  fi
  [[ -x "${JAVA_HOME:-}/bin/java" ]] || { printf '%s\n' "A valid JAVA_HOME is required for Android verification." >&2; exit 1; }
  if [[ -z "${ANDROID_HOME:-}" && -d "$HOME/Library/Android/sdk" ]]; then
    export ANDROID_HOME="$HOME/Library/Android/sdk"
  fi
  [[ -d "${ANDROID_HOME:-}" ]] || { printf '%s\n' "A valid ANDROID_HOME is required for Android verification." >&2; exit 1; }
  (cd "$ANDROID_ROOT" && ./gradlew test assembleRelease)
fi

printf '%s\n' "Health, readiness, proxy tests, Android tests/build, and admin registry checks passed."
