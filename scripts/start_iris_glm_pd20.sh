#!/usr/bin/env bash
# Start the Iris GLM auto-reply build on the dedicated PD20 device.
#
# This script never receives, prints, or writes the API token.  Put a newly
# issued token in /data/local/private/iris-glm.token first as documented in
# docs/GLM_자동응답_운영설정.md.
set -euo pipefail

ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
APK="${APK:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/Iris/output/Iris-release.apk}"

REMOTE_APK=/data/local/tmp/Iris-glm.apk
TOKEN_FILE=/data/local/private/iris-glm.token
ADMIN_FILE=/data/local/private/iris-bot-admins.txt
GENERAL_CONVERSATION_BLOCK_FILE=/data/local/private/iris-general-conversation-blocks.txt
MEMORY_FILE=/data/local/private/iris-bot-memory.json
IMAGE_PROXY_SECRET_FILE=/data/local/private/iris-image-proxy.token
IMAGE_STATE_FILE=/data/local/private/iris-image-jobs.json
IMAGE_PROXY_SECRET_LOCAL="${IMAGE_PROXY_SECRET_LOCAL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/server/proxy-manager/runtime/secrets/route.secret}"
STARTUP_LOG=/data/local/tmp/iris-glm-startup.log

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -x "$ADB" ]] || fail "adb not found: $ADB"
[[ -f "$APK" ]] || fail "release APK not found: $APK"
"$ADB" -s "$SERIAL" get-state | grep -qx 'device' || fail "PD20 is not connected: $SERIAL"

private_metadata="$($ADB -s "$SERIAL" shell "su root sh -c 'stat -c \"%a %U:%G\" /data/local/private 2>/dev/null || echo missing'" | tr -d '\r')"
[[ "$private_metadata" == "700 root:root" ]] ||
  fail "/data/local/private must be mode 700 and root:root (current: $private_metadata)"

# Check only metadata.  Token contents must never pass through a command,
# stdout, shell history, log, APK, or this source file.
token_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ ! -s $TOKEN_FILE ]; then echo missing; exit 0; fi
  stat -c \"%a %U:%G\" $TOKEN_FILE
'" | tr -d '\r')"
[[ "$token_metadata" != "missing" ]] || fail "GLM token file is missing or empty: $TOKEN_FILE"
[[ "$token_metadata" == "600 root:root" ]] || fail "GLM token file must be mode 600 and root:root (current: $token_metadata)"

[[ -s "$IMAGE_PROXY_SECRET_LOCAL" ]] ||
  fail "Image proxy route secret is missing: $IMAGE_PROXY_SECRET_LOCAL"
"$ADB" -s "$SERIAL" push "$IMAGE_PROXY_SECRET_LOCAL" /data/local/tmp/iris-image-proxy.token >/dev/null
"$ADB" -s "$SERIAL" shell "su root sh -c '
  cp /data/local/tmp/iris-image-proxy.token $IMAGE_PROXY_SECRET_FILE
  chown root:root $IMAGE_PROXY_SECRET_FILE
  chmod 600 $IMAGE_PROXY_SECRET_FILE
  rm -f /data/local/tmp/iris-image-proxy.token
'"

# Admin IDs are optional at process level, but an existing file must be private.
# The script never prints its numeric contents.
admin_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ ! -e $ADMIN_FILE ]; then echo missing; exit 0; fi
  stat -c \"%a %U:%G\" $ADMIN_FILE
'" | tr -d '\r')"
if [[ "$admin_metadata" == "missing" ]]; then
  printf 'WARNING: admin commands will be disabled; file is missing: %s\n' "$ADMIN_FILE" >&2
else
  [[ "$admin_metadata" == "600 root:root" ]] ||
    fail "Admin ID file must be mode 600 and root:root (current: $admin_metadata)"
fi

# General conversation is fail-closed unless an explicit root-only block policy
# file exists. An empty file means no users are blocked.
general_block_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ ! -e $GENERAL_CONVERSATION_BLOCK_FILE ]; then
    touch $GENERAL_CONVERSATION_BLOCK_FILE
    chown root:root $GENERAL_CONVERSATION_BLOCK_FILE
    chmod 600 $GENERAL_CONVERSATION_BLOCK_FILE
  fi
  stat -c \"%a %U:%G\" $GENERAL_CONVERSATION_BLOCK_FILE
'" | tr -d '\r')"
[[ "$general_block_metadata" == "600 root:root" ]] ||
  fail "General conversation block file must be mode 600 and root:root (current: $general_block_metadata)"

# The memory file is created atomically by Iris. Validate metadata only when it
# already exists from a previous run.
memory_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ ! -e $MEMORY_FILE ]; then echo missing; exit 0; fi
  stat -c \"%a %U:%G\" $MEMORY_FILE
'" | tr -d '\r')"
if [[ "$memory_metadata" != "missing" && "$memory_metadata" != "600 root:root" ]]; then
  fail "Memory file must be mode 600 and root:root (current: $memory_metadata)"
fi

printf 'Deploying %s to PD20…\n' "$(basename "$APK")"
"$ADB" -s "$SERIAL" push "$APK" "$REMOTE_APK" >/dev/null

# Keep stop and start in different remote shells.  A combined `pkill -f` call
# can match the future app_process command and terminate the startup shell.
"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[p]arty.qwer.iris\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  rm -f $STARTUP_LOG
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18226456888539938,18243496625741211,18393359886930036 \\
  IRIS_GLM_API_KEY_FILE=$TOKEN_FILE \\
  IRIS_GLM_TIMEOUT_MS=120000 \\
  IRIS_GENERAL_CONVERSATION_TIMEOUT_MS=15000 \\
  IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS=18480337854645134,18226456888539938,18243496625741211 \\
  IRIS_GENERAL_CONVERSATION_BLOCK_FILE=$GENERAL_CONVERSATION_BLOCK_FILE \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_WINDOW_MS=300000 \\
  IRIS_GENERAL_CONVERSATION_CIRCUIT_FAILURE_THRESHOLD=3 \\
  IRIS_GLM_MAX_TOKENS=128 \\
  IRIS_GLM_TEMPERATURE=0.2 \\
  IRIS_GLM_RATE_LIMIT_RETRIES=2 \\
  IRIS_GLM_ROOM_QUEUE_CAPACITY=8 \\
  IRIS_GLM_TOTAL_QUEUE_CAPACITY=24 \\
  IRIS_GLM_MAX_CONCURRENCY=2 \\
  IRIS_GLM_ROOM_RATE_WINDOW_MS=30000 \\
  IRIS_GLM_ROOM_RATE_MAX=3 \\
  IRIS_GLM_USER_RATE_WINDOW_MS=60000 \\
  IRIS_GLM_USER_RATE_MAX=5 \\
  IRIS_GLM_DUPLICATE_WINDOW_MS=8000 \\
  IRIS_GLM_MEMORY_FILE=$MEMORY_FILE \\
  IRIS_GLM_MEMORY_MAX_TURNS=4 \\
  IRIS_GLM_MEMORY_TTL_MS=1800000 \\
  IRIS_GLM_MEMORY_MAX_BYTES=1048576 \\
  IRIS_GLM_MEMORY_MAX_CONVERSATIONS=512 \\
  IRIS_BOT_ADMIN_USER_IDS_FILE=$ADMIN_FILE \\
  IRIS_BOT_ADMIN_CONTROL_CHAT_ID=18480337854645134 \\
  IRIS_IMAGE_PROXY_ENABLED=true \\
  IRIS_IMAGE_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_IMAGE_PROXY_SECRET_FILE=$IMAGE_PROXY_SECRET_FILE \\
  IRIS_IMAGE_ALLOWED_CHAT_IDS=18480337854645134,18226456888539938,18243496625741211 \\
  IRIS_IMAGE_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_IMAGE_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_IMAGE_PROXY_JOB_TIMEOUT_MS=1800000 \\
  IRIS_IMAGE_PROMPT_MAX_CHARS=1000 \\
  IRIS_IMAGE_MAX_BYTES=12582912 \\
  IRIS_IMAGE_DELIVERY_CONFIRM_TIMEOUT_MS=45000 \\
  IRIS_IMAGE_MAX_PENDING_PER_ROOM=3 \\
  IRIS_IMAGE_RATE_WINDOW_MS=600000 \\
  IRIS_IMAGE_ROOM_RATE_MAX=3 \\
  IRIS_IMAGE_USER_RATE_MAX=2 \\
  IRIS_IMAGE_STATE_FILE=$IMAGE_STATE_FILE \\
  CLASSPATH=$REMOTE_APK \\
  app_process / party.qwer.iris.Main \\
  > $STARTUP_LOG 2>&1 &
'"

sleep 2
startup_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -E \"GLM auto-reply (enabled|disabled)\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ "$startup_mode" == "GLM auto-reply enabled" ]] || fail "Iris did not enable GLM (startup status: ${startup_mode:-not found})"
scheduler_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"GLM P1 scheduler ready\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ -n "$scheduler_mode" ]] || fail "Iris P1 scheduler readiness was not logged"

"$ADB" -s "$SERIAL" reverse tcp:4340 tcp:4340 >/dev/null
"$ADB" -s "$SERIAL" forward tcp:3000 tcp:3000 >/dev/null
health_ready=false
for _ in {1..10}; do
  if curl --fail --silent --max-time 2 http://127.0.0.1:3000/config >/dev/null 2>&1; then
    health_ready=true
    break
  fi
  sleep 1
done
[[ "$health_ready" == "true" ]] || fail "Iris /config health check did not become ready"

printf '%s\n' 'Iris GLM is running. From a non-bot account, send: 헤이봇 안녕'
