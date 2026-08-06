#!/usr/bin/env bash
# Explicit one-shot R01 YouTube command -> proxy MP4 -> Kakao video DB confirmation.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
APK="${APK:-$ROOT/vendor/android/output/Iris-release.apk}"
REMOTE_APK=/data/local/tmp/Iris-glm.apk
REMOTE_REPORT=/data/local/private/iris-live-youtube-canary-report.log
COMMAND="${IRIS_LIVE_YOUTUBE_CANARY_COMMAND:-헤이봇 유튜브 다운로드 https://www.youtube.com/watch?v=-Yzp92fX_aU}"
COMMAND_BASE64="$(printf '%s' "$COMMAND" | base64 | tr -d '\r\n')"

[[ -x "$ADB" ]] || { printf 'ERROR: adb not found: %s\n' "$ADB" >&2; exit 1; }
[[ -s "$APK" ]] || { printf 'ERROR: release APK not found: %s\n' "$APK" >&2; exit 1; }
"$ADB" -s "$SERIAL" get-state | grep -qx device || {
  printf 'ERROR: PD20 is not connected: %s\n' "$SERIAL" >&2
  exit 1
}

"$ADB" -s "$SERIAL" reverse tcp:4340 tcp:4340 >/dev/null
"$ADB" -s "$SERIAL" push "$APK" "$REMOTE_APK" >/dev/null
"$ADB" -s "$SERIAL" shell "su root sh -c '
  : > $REMOTE_REPORT
  chown root:root $REMOTE_REPORT
  chmod 600 $REMOTE_REPORT
  (
  IRIS_CONFIG_PATH=/data/local/private/iris-config.json \\
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GLM_API_KEY_FILE=/data/local/private/iris-glm.token \\
  IRIS_BOT_ADMIN_CONTROL_CHAT_ID=18480337854645134 \\
  IRIS_BOT_ROOM_POLICY_FILE=/data/local/private/iris-room-capabilities.json \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_ENABLED=true \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_SECRET_FILE=/data/local/private/iris-youtube-proxy.token \\
  IRIS_YOUTUBE_DOWNLOAD_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_YOUTUBE_DOWNLOAD_PROXY_JOB_TIMEOUT_MS=600000 \\
  IRIS_YOUTUBE_DOWNLOAD_MAX_BYTES=52428800 \\
  IRIS_YOUTUBE_DOWNLOAD_MAX_PENDING_PER_ROOM=1 \\
  IRIS_YOUTUBE_DOWNLOAD_RATE_WINDOW_MS=600000 \\
  IRIS_YOUTUBE_DOWNLOAD_ROOM_RATE_MAX=1 \\
  IRIS_YOUTUBE_DOWNLOAD_USER_RATE_MAX=1 \\
  IRIS_LIVE_YOUTUBE_CANARY_CONFIRM=SEND_R01_YOUTUBE_DOWNLOAD \\
  IRIS_LIVE_YOUTUBE_CANARY_COMMAND_B64=$COMMAND_BASE64 \\
  IRIS_LIVE_YOUTUBE_CANARY_REUSE_JOB_ID=${IRIS_LIVE_YOUTUBE_CANARY_REUSE_JOB_ID:-} \\
  CLASSPATH=$REMOTE_APK \\
  app_process / ai.coreline.heybot.Main --live-youtube-canary
  ) > $REMOTE_REPORT 2>&1 &
  echo "LIVE_YOUTUBE_CANARY_PID=\$!"
'" | tr -d '\r'
printf 'Live canary started. Read the authenticated device report: %s\n' "$REMOTE_REPORT"
