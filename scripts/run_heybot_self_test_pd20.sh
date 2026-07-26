#!/usr/bin/env bash
# Run the Android self-test entrypoint without stopping the live Iris process.
# QUICK has no network/database write side effects. CANARY is guarded in-app.
set -euo pipefail

ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
MODE="${1:-quick}"
APK="${APK:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/android/output/Iris-release.apk}"
REMOTE_APK="${REMOTE_APK:-/data/local/tmp/Iris-glm.apk}"

case "$MODE" in
  quick|integration|device|canary) ;;
  *)
    printf 'Usage: %s [quick|integration|device|canary]\n' "$0" >&2
    exit 2
    ;;
esac

[[ -x "$ADB" ]] || { printf 'ERROR: adb not found: %s\n' "$ADB" >&2; exit 1; }
[[ -f "$APK" ]] || { printf 'ERROR: release APK not found: %s\n' "$APK" >&2; exit 1; }
"$ADB" -s "$SERIAL" get-state | grep -qx device || {
  printf 'ERROR: PD20 is not connected: %s\n' "$SERIAL" >&2
  exit 1
}

if [[ "$MODE" == integration || "$MODE" == canary ]]; then
  "$ADB" -s "$SERIAL" reverse tcp:4340 tcp:4340 >/dev/null
fi

printf 'Running HeyBot Android self-test mode=%s on %s…\n' "$MODE" "$SERIAL"
"$ADB" -s "$SERIAL" push "$APK" "$REMOTE_APK" >/dev/null
"$ADB" -s "$SERIAL" shell "su root sh -c '
  IRIS_CONVERSATION_PROXY_ENABLED=true \\
  IRIS_CONVERSATION_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  CLASSPATH=$REMOTE_APK \\
  app_process / ai.coreline.heybot.Main --self-test $MODE
'" | tr -d '\r'
