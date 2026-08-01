#!/usr/bin/env bash
# Start the Iris GLM auto-reply build on the dedicated PD20 device.
#
# This script never receives, prints, or writes the API token.  Put a newly
# issued token in /data/local/private/iris-glm.token first as documented in
# docs/GLM_자동응답_운영설정.md.
set -euo pipefail

ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
APK="${APK:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/android/output/Iris-release.apk}"

REMOTE_APK=/data/local/tmp/Iris-glm.apk
TOKEN_FILE=/data/local/private/iris-glm.token
ADMIN_FILE=/data/local/private/iris-bot-admins.txt
GENERAL_CONVERSATION_BLOCK_FILE=/data/local/private/iris-general-conversation-blocks.txt
GENERAL_CONVERSATION_MODE_FILE=/data/local/private/iris-general-conversation-mode.json
ROOM_CAPABILITY_POLICY_FILE=/data/local/private/iris-room-capabilities.json
MEMORY_FILE=/data/local/private/iris-bot-memory.json
IMAGE_PROXY_SECRET_FILE=/data/local/private/iris-image-proxy.token
IMAGE_STATE_FILE=/data/local/private/iris-image-jobs.json
VISION_PROXY_SECRET_FILE=/data/local/private/iris-vision-proxy.token
VIDEO_PROXY_SECRET_FILE=/data/local/private/iris-video-proxy.token
VIDEO_STATE_FILE=/data/local/private/iris-video-jobs.json
PEN_BRUSH_PROXY_SECRET_FILE=/data/local/private/iris-pen-brush-proxy.token
PEN_BRUSH_STATE_FILE=/data/local/private/iris-pen-brush-jobs.json
CONVERSATION_PROXY_SECRET_FILE=/data/local/private/iris-conversation-proxy.token
CONVERSATION_ENGINE_FILE=/data/local/private/iris-conversation-engine.conf
CONFIG_FILE=/data/local/private/iris-config.json
HTTP_ADMIN_SECRET_FILE=/data/local/private/iris-http-admin.token
HTTP_API_ENABLED="${IRIS_HTTP_API_ENABLED:-false}"
IMAGE_PROXY_SECRET_LOCAL="${IMAGE_PROXY_SECRET_LOCAL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/vendor/server/proxy-manager/runtime/secrets/route.secret}"
VISION_PROXY_SECRET_LOCAL="${VISION_PROXY_SECRET_LOCAL:-$IMAGE_PROXY_SECRET_LOCAL}"
VIDEO_PROXY_SECRET_LOCAL="${VIDEO_PROXY_SECRET_LOCAL:-$IMAGE_PROXY_SECRET_LOCAL}"
PEN_BRUSH_PROXY_SECRET_LOCAL="${PEN_BRUSH_PROXY_SECRET_LOCAL:-$IMAGE_PROXY_SECRET_LOCAL}"
CONVERSATION_PROXY_SECRET_LOCAL="${CONVERSATION_PROXY_SECRET_LOCAL:-$IMAGE_PROXY_SECRET_LOCAL}"
# 영상·펜브러쉬는 방 단위 capability 정책이 실제 허용 범위를 결정한다. 이 배포
# 스크립트가 기본 false로 실행되면 APK·프록시가 정상이어도 명령이 조용히
# 무시되므로, 운영 기본값은 켜 둔다. 긴급 중지는 명시적으로 false를 전달한다.
VIDEO_PROXY_ENABLED="${IRIS_VIDEO_PROXY_ENABLED:-true}"
PEN_BRUSH_PROXY_ENABLED="${IRIS_PEN_BRUSH_PROXY_ENABLED:-true}"
CONVERSATION_PROXY_ENABLED="${IRIS_CONVERSATION_PROXY_ENABLED:-true}"
VISION_PROXY_ENABLED="${IRIS_VISION_PROXY_ENABLED:-true}"
ROOM_CAPABILITY_BOOTSTRAP_LOCAL="${ROOM_CAPABILITY_BOOTSTRAP_LOCAL:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/config/iris-room-capabilities.bootstrap.json}"
STARTUP_LOG=/data/local/private/iris-glm-startup.log

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ -x "$ADB" ]] || fail "adb not found: $ADB"
[[ -f "$APK" ]] || fail "release APK not found: $APK"
"$ADB" -s "$SERIAL" get-state | grep -qx 'device' || fail "PD20 is not connected: $SERIAL"
# Remove the legacy permanent host-side management tunnel before every secure
# deployment. Image generation continues to use the separate reverse 4340 map.
"$ADB" -s "$SERIAL" forward --remove tcp:3000 >/dev/null 2>&1 || true

private_metadata="$($ADB -s "$SERIAL" shell "su root sh -c 'stat -c \"%a %U:%G\" /data/local/private 2>/dev/null || echo missing'" | tr -d '\r')"
[[ "$private_metadata" == "700 root:root" ]] ||
  fail "/data/local/private must be mode 700 and root:root (current: $private_metadata)"

[[ "$HTTP_API_ENABLED" == "true" || "$HTTP_API_ENABLED" == "false" ]] ||
  fail "IRIS_HTTP_API_ENABLED must be true or false"
[[ "$VIDEO_PROXY_ENABLED" == "true" || "$VIDEO_PROXY_ENABLED" == "false" ]] ||
  fail "IRIS_VIDEO_PROXY_ENABLED must be true or false"
[[ "$PEN_BRUSH_PROXY_ENABLED" == "true" || "$PEN_BRUSH_PROXY_ENABLED" == "false" ]] ||
  fail "IRIS_PEN_BRUSH_PROXY_ENABLED must be true or false"
[[ "$CONVERSATION_PROXY_ENABLED" == "true" || "$CONVERSATION_PROXY_ENABLED" == "false" ]] ||
  fail "IRIS_CONVERSATION_PROXY_ENABLED must be true or false"
[[ "$VISION_PROXY_ENABLED" == "true" || "$VISION_PROXY_ENABLED" == "false" ]] ||
  fail "IRIS_VISION_PROXY_ENABLED must be true or false"

config_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ -L $CONFIG_FILE ]; then echo symlink; exit 0; fi
  if [ ! -e $CONFIG_FILE ]; then
    touch $CONFIG_FILE
    chown root:root $CONFIG_FILE
    chmod 600 $CONFIG_FILE
  fi
  stat -c \"%a %U:%G\" $CONFIG_FILE
'" | tr -d '\r')"
[[ "$config_metadata" == "600 root:root" ]] ||
  fail "Iris config must be mode 600 and root:root (current: $config_metadata)"

startup_log_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ -L $STARTUP_LOG ]; then echo symlink; exit 0; fi
  if [ ! -e $STARTUP_LOG ]; then
    touch $STARTUP_LOG
    chown root:root $STARTUP_LOG
    chmod 600 $STARTUP_LOG
  fi
  stat -c \"%a %U:%G\" $STARTUP_LOG
'" | tr -d '\r')"
[[ "$startup_log_metadata" == "600 root:root" ]] ||
  fail "Iris startup log must be mode 600 and root:root (current: $startup_log_metadata)"

if [[ "$HTTP_API_ENABLED" == "true" ]]; then
  http_secret_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
    if [ -L $HTTP_ADMIN_SECRET_FILE ]; then echo symlink; exit 0; fi
    if [ ! -s $HTTP_ADMIN_SECRET_FILE ]; then echo missing; exit 0; fi
    stat -c \"%a %U:%G\" $HTTP_ADMIN_SECRET_FILE
  '" | tr -d '\r')"
  [[ "$http_secret_metadata" == "600 root:root" ]] ||
    fail "HTTP admin secret must be mode 600 and root:root (current: $http_secret_metadata)"
fi

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
[[ -s "$ROOM_CAPABILITY_BOOTSTRAP_LOCAL" ]] ||
  fail "Room capability bootstrap policy is missing: $ROOM_CAPABILITY_BOOTSTRAP_LOCAL"
python3 -m json.tool "$ROOM_CAPABILITY_BOOTSTRAP_LOCAL" >/dev/null ||
  fail "Room capability bootstrap policy is not valid JSON: $ROOM_CAPABILITY_BOOTSTRAP_LOCAL"
"$ADB" -s "$SERIAL" push "$IMAGE_PROXY_SECRET_LOCAL" /data/local/tmp/iris-image-proxy.token >/dev/null
"$ADB" -s "$SERIAL" shell "su root sh -c '
  cp /data/local/tmp/iris-image-proxy.token $IMAGE_PROXY_SECRET_FILE
  chown root:root $IMAGE_PROXY_SECRET_FILE
  chmod 600 $IMAGE_PROXY_SECRET_FILE
  rm -f /data/local/tmp/iris-image-proxy.token
'"

if [[ "$VISION_PROXY_ENABLED" == "true" ]]; then
  [[ -s "$VISION_PROXY_SECRET_LOCAL" ]] ||
    fail "Vision proxy route secret is missing: $VISION_PROXY_SECRET_LOCAL"
  "$ADB" -s "$SERIAL" push "$VISION_PROXY_SECRET_LOCAL" /data/local/tmp/iris-vision-proxy.token >/dev/null
  "$ADB" -s "$SERIAL" shell "su root sh -c '
    cp /data/local/tmp/iris-vision-proxy.token $VISION_PROXY_SECRET_FILE
    chown root:root $VISION_PROXY_SECRET_FILE
    chmod 600 $VISION_PROXY_SECRET_FILE
    rm -f /data/local/tmp/iris-vision-proxy.token
  '"
fi

if [[ "$PEN_BRUSH_PROXY_ENABLED" == "true" ]]; then
  [[ -s "$PEN_BRUSH_PROXY_SECRET_LOCAL" ]] ||
    fail "Pen-brush proxy route secret is missing: $PEN_BRUSH_PROXY_SECRET_LOCAL"
  "$ADB" -s "$SERIAL" push "$PEN_BRUSH_PROXY_SECRET_LOCAL" /data/local/tmp/iris-pen-brush-proxy.token >/dev/null
  "$ADB" -s "$SERIAL" shell "su root sh -c '
    cp /data/local/tmp/iris-pen-brush-proxy.token $PEN_BRUSH_PROXY_SECRET_FILE
    chown root:root $PEN_BRUSH_PROXY_SECRET_FILE
    chmod 600 $PEN_BRUSH_PROXY_SECRET_FILE
    rm -f /data/local/tmp/iris-pen-brush-proxy.token
  '"
fi

if [[ "$VIDEO_PROXY_ENABLED" == "true" ]]; then
  [[ -s "$VIDEO_PROXY_SECRET_LOCAL" ]] ||
    fail "Video proxy route secret is missing: $VIDEO_PROXY_SECRET_LOCAL"
  "$ADB" -s "$SERIAL" push "$VIDEO_PROXY_SECRET_LOCAL" /data/local/tmp/iris-video-proxy.token >/dev/null
  "$ADB" -s "$SERIAL" shell "su root sh -c '
    cp /data/local/tmp/iris-video-proxy.token $VIDEO_PROXY_SECRET_FILE
    chown root:root $VIDEO_PROXY_SECRET_FILE
    chmod 600 $VIDEO_PROXY_SECRET_FILE
    rm -f /data/local/tmp/iris-video-proxy.token
  '"
fi

if [[ "$CONVERSATION_PROXY_ENABLED" == "true" ]]; then
  [[ -s "$CONVERSATION_PROXY_SECRET_LOCAL" ]] ||
    fail "Conversation proxy route secret is missing: $CONVERSATION_PROXY_SECRET_LOCAL"
  "$ADB" -s "$SERIAL" push "$CONVERSATION_PROXY_SECRET_LOCAL" /data/local/tmp/iris-conversation-proxy.token >/dev/null
  "$ADB" -s "$SERIAL" shell "su root sh -c '
    cp /data/local/tmp/iris-conversation-proxy.token $CONVERSATION_PROXY_SECRET_FILE
    chown root:root $CONVERSATION_PROXY_SECRET_FILE
    chmod 600 $CONVERSATION_PROXY_SECRET_FILE
    rm -f /data/local/tmp/iris-conversation-proxy.token
    if [ ! -e $CONVERSATION_ENGINE_FILE ]; then
      printf "schemaVersion=1\\nengine=GLM\\nupdatedAt=0\\n" > $CONVERSATION_ENGINE_FILE
      chown root:root $CONVERSATION_ENGINE_FILE
      chmod 600 $CONVERSATION_ENGINE_FILE
    fi
  '"
fi

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

# The dynamic room policy persists administrator changes. Bootstrap it only on
# first deployment; never overwrite a policy changed from the control room.
room_policy_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ ! -e $ROOM_CAPABILITY_POLICY_FILE ]; then echo missing; exit 0; fi
  stat -c \"%a %U:%G\" $ROOM_CAPABILITY_POLICY_FILE
'" | tr -d '\r')"
if [[ "$room_policy_metadata" == "missing" ]]; then
  "$ADB" -s "$SERIAL" push "$ROOM_CAPABILITY_BOOTSTRAP_LOCAL" /data/local/tmp/iris-room-capabilities.json >/dev/null
  "$ADB" -s "$SERIAL" shell "su root sh -c '
    cp /data/local/tmp/iris-room-capabilities.json $ROOM_CAPABILITY_POLICY_FILE
    chown root:root $ROOM_CAPABILITY_POLICY_FILE
    chmod 600 $ROOM_CAPABILITY_POLICY_FILE
    rm -f /data/local/tmp/iris-room-capabilities.json
  '"
elif [[ "$room_policy_metadata" != "600 root:root" ]]; then
  fail "Room capability policy must be mode 600 and root:root (current: $room_policy_metadata)"
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

# The app owns this atomic state file. A missing file intentionally means OFF
# on first migration; deployments must never create or overwrite it.
general_mode_metadata="$($ADB -s "$SERIAL" shell "su root sh -c '
  if [ -L $GENERAL_CONVERSATION_MODE_FILE ]; then echo symlink; exit 0; fi
  if [ ! -e $GENERAL_CONVERSATION_MODE_FILE ]; then echo missing; exit 0; fi
  stat -c \"%a %U:%G\" $GENERAL_CONVERSATION_MODE_FILE
'" | tr -d '\r')"
if [[ "$general_mode_metadata" != "missing" && "$general_mode_metadata" != "600 root:root" ]]; then
  fail "General conversation mode file must be mode 600 and root:root (current: $general_mode_metadata)"
fi

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
"$ADB" -s "$SERIAL" shell "su root sh -c 'pkill -f \"[a]i.coreline.heybot\" 2>/dev/null || true'"
"$ADB" -s "$SERIAL" shell "su root sh -c '
  : > $STARTUP_LOG
  chown root:root $STARTUP_LOG
  chmod 600 $STARTUP_LOG
  IRIS_CONFIG_PATH=$CONFIG_FILE \\
  IRIS_HTTP_API_ENABLED=$HTTP_API_ENABLED \\
  IRIS_HTTP_ADMIN_SECRET_FILE=$HTTP_ADMIN_SECRET_FILE \\
  IRIS_GLM_ENABLED=true \\
  IRIS_GLM_BASE_URL=https://api.z.ai/api/paas/v4/ \\
  IRIS_GLM_MODEL=glm-4.5-flash \\
  IRIS_GLM_TRIGGER=헤이봇 \\
  IRIS_GLM_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GLM_API_KEY_FILE=$TOKEN_FILE \\
  IRIS_GLM_TIMEOUT_MS=120000 \\
  IRIS_GENERAL_CONVERSATION_TIMEOUT_MS=15000 \\
  IRIS_GENERAL_CONVERSATION_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_GENERAL_CONVERSATION_BLOCK_FILE=$GENERAL_CONVERSATION_BLOCK_FILE \\
  IRIS_GENERAL_CONVERSATION_MODE_FILE=$GENERAL_CONVERSATION_MODE_FILE \\
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
  IRIS_BOT_ROOM_POLICY_FILE=$ROOM_CAPABILITY_POLICY_FILE \\
  IRIS_IMAGE_PROXY_ENABLED=true \\
  IRIS_IMAGE_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_IMAGE_PROXY_SECRET_FILE=$IMAGE_PROXY_SECRET_FILE \\
  IRIS_IMAGE_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
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
  IRIS_VISION_PROXY_ENABLED=$VISION_PROXY_ENABLED \\
  IRIS_VISION_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_VISION_PROXY_SECRET_FILE=$VISION_PROXY_SECRET_FILE \\
  IRIS_VISION_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_VISION_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_VISION_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_VISION_PROXY_JOB_TIMEOUT_MS=120000 \\
  IRIS_VISION_RECENT_IMAGE_WINDOW_MS=1800000 \\
  IRIS_VISION_MAX_PENDING_PER_ROOM=1 \\
  IRIS_VISION_RATE_WINDOW_MS=600000 \\
  IRIS_VISION_ROOM_RATE_MAX=3 \\
  IRIS_VISION_USER_RATE_MAX=2 \\
  IRIS_VIDEO_PROXY_ENABLED=$VIDEO_PROXY_ENABLED \\
  IRIS_VIDEO_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_VIDEO_PROXY_SECRET_FILE=$VIDEO_PROXY_SECRET_FILE \\
  IRIS_VIDEO_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_VIDEO_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_VIDEO_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_VIDEO_PROXY_JOB_TIMEOUT_MS=1800000 \\
  IRIS_VIDEO_PROMPT_MAX_CHARS=1000 \\
  IRIS_VIDEO_MAX_BYTES=52428800 \\
  IRIS_VIDEO_DELIVERY_CONFIRM_TIMEOUT_MS=45000 \\
  IRIS_VIDEO_MAX_PENDING_PER_ROOM=1 \\
  IRIS_VIDEO_RATE_WINDOW_MS=600000 \\
  IRIS_VIDEO_ROOM_RATE_MAX=1 \\
  IRIS_VIDEO_USER_RATE_MAX=1 \\
  IRIS_VIDEO_STATE_FILE=$VIDEO_STATE_FILE \\
  IRIS_PEN_BRUSH_PROXY_ENABLED=$PEN_BRUSH_PROXY_ENABLED \\
  IRIS_PEN_BRUSH_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_PEN_BRUSH_PROXY_SECRET_FILE=$PEN_BRUSH_PROXY_SECRET_FILE \\
  IRIS_PEN_BRUSH_ALLOWED_CHAT_IDS=18480337854645134,18393359886930036,18243496625741211,18226456888539938 \\
  IRIS_PEN_BRUSH_PROXY_REQUEST_TIMEOUT_MS=30000 \\
  IRIS_PEN_BRUSH_PROXY_POLL_INTERVAL_MS=1000 \\
  IRIS_PEN_BRUSH_PROXY_JOB_TIMEOUT_MS=1800000 \\
  IRIS_PEN_BRUSH_PROMPT_MAX_CHARS=300 \\
  IRIS_PEN_BRUSH_MAX_BYTES=33554432 \\
  IRIS_PEN_BRUSH_DELIVERY_CONFIRM_TIMEOUT_MS=45000 \\
  IRIS_PEN_BRUSH_MAX_PENDING_PER_ROOM=1 \\
  IRIS_PEN_BRUSH_RATE_WINDOW_MS=600000 \\
  IRIS_PEN_BRUSH_ROOM_RATE_MAX=1 \\
  IRIS_PEN_BRUSH_USER_RATE_MAX=1 \\
  IRIS_PEN_BRUSH_STATE_FILE=$PEN_BRUSH_STATE_FILE \\
  IRIS_CONVERSATION_PROXY_ENABLED=$CONVERSATION_PROXY_ENABLED \\
  IRIS_CONVERSATION_PROXY_BASE_URL=http://127.0.0.1:4340 \\
  IRIS_CONVERSATION_PROXY_SECRET_FILE=$CONVERSATION_PROXY_SECRET_FILE \\
  IRIS_CONVERSATION_ENGINE_FILE=$CONVERSATION_ENGINE_FILE \\
  IRIS_CONVERSATION_PROXY_TIMEOUT_MS=100000 \\
  CLASSPATH=$REMOTE_APK \\
  app_process / ai.coreline.heybot.Main \\
  > $STARTUP_LOG 2>&1 &
'"

for _ in {1..30}; do
  startup_ready="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Iris process lifetime ready\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$startup_ready" ]] && break
  sleep 0.5
done
startup_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -E \"GLM auto-reply (enabled|disabled)\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ "$startup_mode" == "GLM auto-reply enabled" ]] || fail "Iris did not enable GLM (startup status: ${startup_mode:-not found})"
scheduler_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"GLM P1 scheduler ready\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ -n "$scheduler_mode" ]] || fail "Iris P1 scheduler readiness was not logged"
room_policy_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Room capability policy ready=true rooms=4\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ -n "$room_policy_mode" ]] || fail "Room capability policy did not become ready"
general_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"General conversation mode restored=\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
[[ -n "$general_mode" ]] || fail "General conversation mode state was not restored"
if [[ "$VIDEO_PROXY_ENABLED" == "true" ]]; then
  video_proxy_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Video proxy coordinator ready\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$video_proxy_mode" ]] || fail "Video proxy coordinator did not become ready"
fi
if [[ "$PEN_BRUSH_PROXY_ENABLED" == "true" ]]; then
  pen_brush_proxy_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"PenBrush proxy coordinator ready\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$pen_brush_proxy_mode" ]] || fail "Pen-brush proxy coordinator did not become ready"
fi
if [[ "$CONVERSATION_PROXY_ENABLED" == "true" ]]; then
  conversation_proxy_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Conversation engine ready=\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$conversation_proxy_mode" ]] || fail "Conversation engine mode was not initialized"
fi
if [[ "$VISION_PROXY_ENABLED" == "true" ]]; then
  vision_proxy_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Vision proxy enabled\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$vision_proxy_mode" ]] || fail "Vision proxy was not enabled"
fi
if [[ "$HTTP_API_ENABLED" == "false" ]]; then
  http_mode="$($ADB -s "$SERIAL" shell "su root sh -c 'grep -F \"Iris HTTP API disabled\" $STARTUP_LOG | tail -1'" | tr -d '\r')"
  [[ -n "$http_mode" ]] || fail "Iris HTTP API did not fail closed"
fi

iris_pid="$($ADB -s "$SERIAL" shell "su root sh -c 'pgrep -f \"[a]i.coreline.heybot\" | head -1'" | tr -d '\r')"
[[ -n "$iris_pid" ]] || fail "Iris process did not remain running"

"$ADB" -s "$SERIAL" reverse tcp:4340 tcp:4340 >/dev/null

printf '%s\n' 'Iris GLM is running. From a non-bot account, send: 헤이봇 안녕'
