#!/usr/bin/env bash
# One-shot, fixed-scope R01 audio canary. KakaoTalk must already be unlocked.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
APK="${APK:-$ROOT/vendor/android/output/Iris-release.apk}"
FIXTURE="${FIXTURE:-/tmp/heybot-audio-canary.m4a}"
FORMAT="${FORMAT:-${FIXTURE##*.}}"
FORMAT="$(printf '%s' "$FORMAT" | tr '[:upper:]' '[:lower:]')"
ENGINE="$(printf '%s' "${ENGINE:-GLM}" | tr '[:lower:]' '[:upper:]')"
COMMAND_BASE64="$(printf '%s' "${IRIS_LIVE_AUDIO_CANARY_COMMAND:-헤이봇 음성 요약}" | base64 | tr -d '\r\n')"
REMOTE_APK=/data/local/tmp/Iris-glm.apk
REMOTE_FIXTURE="/data/local/tmp/heybot-audio-canary.$FORMAT"
REMOTE_ENGINE_FILE=/data/local/tmp/iris-conversation-engine-canary.conf

cleanup() {
  "$ADB" -s "$SERIAL" shell "su root sh -c 'rm -f $REMOTE_FIXTURE $REMOTE_ENGINE_FILE'" \
    >/dev/null 2>&1 || true
}
trap cleanup EXIT

[[ -x "$ADB" ]] || { printf 'ERROR: adb not found: %s\n' "$ADB" >&2; exit 1; }
[[ -f "$APK" ]] || { printf 'ERROR: release APK not found: %s\n' "$APK" >&2; exit 1; }
[[ "$FORMAT" == "mp3" || "$FORMAT" == "m4a" || "$FORMAT" == "wav" ]] || {
  printf 'ERROR: FORMAT must be mp3, m4a, or wav: %s\n' "$FORMAT" >&2
  exit 1
}
[[ "$ENGINE" == "GLM" || "$ENGINE" == "CODEX" || "$ENGINE" == "GROK" ]] || {
  printf 'ERROR: ENGINE must be GLM, CODEX, or GROK: %s\n' "$ENGINE" >&2
  exit 1
}
[[ -s "$FIXTURE" ]] || { printf 'ERROR: audio fixture not found: %s\n' "$FIXTURE" >&2; exit 1; }
"$ADB" -s "$SERIAL" get-state | grep -qx device || {
  printf 'ERROR: PD20 is not connected: %s\n' "$SERIAL" >&2
  exit 1
}

"$ADB" -s "$SERIAL" reverse tcp:4340 tcp:4340 >/dev/null
"$ADB" -s "$SERIAL" push "$APK" "$REMOTE_APK" >/dev/null
"$ADB" -s "$SERIAL" push "$FIXTURE" "$REMOTE_FIXTURE" >/dev/null
"$ADB" -s "$SERIAL" shell "su root sh -c '
  printf \"schemaVersion=1\\nengine=$ENGINE\\nupdatedAt=0\\n\" > $REMOTE_ENGINE_FILE
  chown root:root $REMOTE_ENGINE_FILE
  chmod 600 $REMOTE_ENGINE_FILE
'"

"$ADB" -s "$SERIAL" shell "su root sh -c '
  IRIS_CONFIG_PATH=/data/local/private/iris-config.json \\
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GLM_API_KEY_FILE=/data/local/private/iris-glm.token \\
  IRIS_BOT_ADMIN_CONTROL_CHAT_ID=18480337854645134 \\
  IRIS_BOT_ROOM_POLICY_FILE=/data/local/private/iris-room-capabilities.json \\
  IRIS_CONVERSATION_PROXY_ENABLED=true \\
  IRIS_CONVERSATION_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_CONVERSATION_PROXY_SECRET_FILE=/data/local/private/iris-conversation-proxy.token \\
  IRIS_CONVERSATION_ENGINE_FILE=$REMOTE_ENGINE_FILE \\
  IRIS_CONVERSATION_PROXY_TIMEOUT_MS=100000 \\
  IRIS_AUDIO_PROXY_ENABLED=true \\
  IRIS_AUDIO_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_AUDIO_PROXY_SECRET_FILE=/data/local/private/iris-audio-proxy.token \\
  IRIS_AUDIO_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_AUDIO_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_AUDIO_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_AUDIO_PROXY_JOB_TIMEOUT_MS=1800000 \\
  IRIS_LIVE_AUDIO_CANARY_CONFIRM=SEND_R01_AUDIO_AND_ANALYZE \\
  IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST=${IRIS_LIVE_AUDIO_CANARY_REUSE_LATEST:-false} \\
  IRIS_LIVE_AUDIO_CANARY_FORMAT=$FORMAT \\
  IRIS_LIVE_AUDIO_CANARY_FILE=$REMOTE_FIXTURE \\
  IRIS_LIVE_AUDIO_CANARY_COMMAND_B64=$COMMAND_BASE64 \\
  IRIS_LIVE_AUDIO_CANARY_VERIFY_CONTROLS=${IRIS_LIVE_AUDIO_CANARY_VERIFY_CONTROLS:-false} \\
  IRIS_LIVE_AUDIO_CANARY_CANCEL_AFTER_START=${IRIS_LIVE_AUDIO_CANARY_CANCEL_AFTER_START:-false} \\
  CLASSPATH=$REMOTE_APK \\
  app_process / ai.coreline.heybot.Main --live-audio-canary
'" | tr -d '\r'
