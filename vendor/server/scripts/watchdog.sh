#!/usr/bin/env bash
set -euo pipefail

ROOT="${HEYBOT_SERVER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="$ROOT/runtime/watchdog"
STARTED_FILE="$STATE_DIR/started-at"
FAILURE_FILE="$STATE_DIR/consecutive-failures"
TRANSPORT_MISSING_FILE="$STATE_DIR/transport-missing-count"
LAST_ADB_RESTART_FILE="$STATE_DIR/last-adb-restart-at"
WIRELESS_ENDPOINT_FILE="$STATE_DIR/wireless-endpoint"
ACTIVE_TARGET_FILE="$STATE_DIR/active-target"
LINK_STATUS_FILE="$STATE_DIR/link-status"
CONNECTION_STATE_FILE="$STATE_DIR/connection-state.json"
LOCK_DIR="$STATE_DIR/run.lock"

THRESHOLD="${WATCHDOG_FAILURE_THRESHOLD:-3}"
GRACE="${WATCHDOG_STARTUP_GRACE_SECONDS:-60}"
LINK_MISSING_THRESHOLD="${PD20_LINK_MISSING_THRESHOLD:-2}"
ADB_RESTART_COOLDOWN="${PD20_ADB_RESTART_COOLDOWN_SECONDS:-300}"
DEVICE_READY_TIMEOUT="${PD20_DEVICE_READY_TIMEOUT_SECONDS:-5}"
DEVICE_READY_URL="${PD20_DEVICE_READY_URL:-http://127.0.0.1:4340/ready}"
WIRELESS_ENABLED="${PD20_WIRELESS_ADB_ENABLED:-false}"

DOMAIN="gui/$(id -u)"
CURL_BIN="${CURL_BIN:-curl}"
LAUNCHCTL_BIN="${LAUNCHCTL_BIN:-launchctl}"
DATE_BIN="${DATE_BIN:-date}"
IOREG_BIN="${IOREG_BIN:-ioreg}"
SLEEP_BIN="${SLEEP_BIN:-sleep}"

ADB="${ADB_BIN:-}"
PD20_SERIAL_VALUE="${PD20_SERIAL:-0123456789ABCDEF}"
PD20_MODEL_VALUE="${PD20_MODEL:-PD20}"
SELECTED_TARGET=""
SELECTED_TRANSPORT="none"
LAST_RECOVERY_ACTION="none"
DEVICE_HTTP_STATUS="000"

mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

log_event() {
  printf '%s watchdog %s\n' "$("$DATE_BIN" -u +%FT%TZ)" "$*"
}

write_atomic() {
  local file="$1"
  local value="$2"
  local tmp="$file.tmp.$$"
  umask 077
  printf '%s\n' "$value" >"$tmp"
  chmod 600 "$tmp"
  mv -f "$tmp" "$file"
}

read_uint() {
  local file="$1"
  local fallback="$2"
  local value="$fallback"
  [[ -s "$file" ]] && value="$(<"$file")"
  [[ "$value" =~ ^[0-9]+$ ]] || value="$fallback"
  printf '%s' "$value"
}

release_lock() {
  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || true
}

acquire_lock() {
  if mkdir "$LOCK_DIR" 2>/dev/null; then
    printf '%s\n' "$$" >"$LOCK_DIR/pid"
    trap release_lock EXIT INT TERM
    return 0
  fi

  local owner=""
  [[ -s "$LOCK_DIR/pid" ]] && owner="$(<"$LOCK_DIR/pid")"
  if [[ "$owner" =~ ^[0-9]+$ ]] && kill -0 "$owner" 2>/dev/null; then
    exit 0
  fi

  rm -f "$LOCK_DIR/pid" 2>/dev/null || true
  rmdir "$LOCK_DIR" 2>/dev/null || exit 0
  mkdir "$LOCK_DIR" 2>/dev/null || exit 0
  printf '%s\n' "$$" >"$LOCK_DIR/pid"
  trap release_lock EXIT INT TERM
}

sanitize_state_value() {
  printf '%s' "$1" | tr -cd 'A-Za-z0-9._:-'
}

update_link_state() {
  local status="$1"
  local target
  local transport
  local action
  local previous=""
  target="$(sanitize_state_value "${2:-none}")"
  transport="$(sanitize_state_value "${3:-none}")"
  action="$(sanitize_state_value "${4:-none}")"
  [[ -s "$LINK_STATUS_FILE" ]] && previous="$(<"$LINK_STATUS_FILE")"

  write_atomic "$LINK_STATUS_FILE" "$status"
  write_atomic "$ACTIVE_TARGET_FILE" "$target"
  write_atomic "$CONNECTION_STATE_FILE" "{\"schemaVersion\":1,\"status\":\"$status\",\"target\":\"$target\",\"transport\":\"$transport\",\"lastAction\":\"$action\",\"updatedAt\":$("$DATE_BIN" +%s)}"
  if [[ "$previous" != "$status" || "$action" != "none" ]]; then
    log_event "PD20 link status=$status transport=$transport action=$action"
  fi
}

adb_state() {
  local target="$1"
  local state=""
  state="$("$ADB" -s "$target" get-state 2>/dev/null | tr -d '\r' || true)"
  case "$state" in
    device|offline|unauthorized) printf '%s' "$state" ;;
    *) printf 'absent' ;;
  esac
}

usb_device_present() {
  [[ -n "$IOREG_BIN" ]] || return 1
  "$IOREG_BIN" -p IOUSB -l -w 0 2>/dev/null |
    grep -Fq "\"USB Serial Number\" = \"$PD20_SERIAL_VALUE\""
}

reset_missing_count() {
  write_atomic "$TRANSPORT_MISSING_FILE" "0"
}

increment_missing_count() {
  local count
  count="$(read_uint "$TRANSPORT_MISSING_FILE" 0)"
  count=$((count + 1))
  write_atomic "$TRANSPORT_MISSING_FILE" "$count"
  printf '%s' "$count"
}

restart_adb_server_if_allowed() {
  local now
  local last
  now="$("$DATE_BIN" +%s)"
  last="$(read_uint "$LAST_ADB_RESTART_FILE" 0)"
  if (( now - last < ADB_RESTART_COOLDOWN )); then
    LAST_RECOVERY_ACTION="adb_restart_cooldown"
    return 1
  fi

  write_atomic "$LAST_ADB_RESTART_FILE" "$now"
  LAST_RECOVERY_ACTION="adb_server_restarted"
  log_event "restarting ADB server for physically present PD20"
  "$ADB" kill-server >/dev/null 2>&1 || true
  "$ADB" start-server >/dev/null 2>&1 || return 1

  local attempt
  for attempt in 1 2 3; do
    [[ "$(adb_state "$PD20_SERIAL_VALUE")" == "device" ]] && return 0
    "$SLEEP_BIN" 1
  done
  return 1
}

select_usb_target() {
  local state
  local missing
  state="$(adb_state "$PD20_SERIAL_VALUE")"

  if [[ "$state" == "device" ]]; then
    reset_missing_count
    SELECTED_TARGET="$PD20_SERIAL_VALUE"
    SELECTED_TRANSPORT="usb"
    return 0
  fi

  if [[ "$state" == "unauthorized" ]]; then
    reset_missing_count
    LAST_RECOVERY_ACTION="usb_unauthorized"
    return 1
  fi

  if [[ "$state" == "offline" ]]; then
    LAST_RECOVERY_ACTION="adb_reconnect"
    "$ADB" -s "$PD20_SERIAL_VALUE" reconnect >/dev/null 2>&1 || true
    if [[ "$(adb_state "$PD20_SERIAL_VALUE")" == "device" ]]; then
      reset_missing_count
      SELECTED_TARGET="$PD20_SERIAL_VALUE"
      SELECTED_TRANSPORT="usb"
      return 0
    fi
  fi

  missing="$(increment_missing_count)"
  if ! usb_device_present; then
    LAST_RECOVERY_ACTION="usb_not_present"
    return 1
  fi

  if (( missing < LINK_MISSING_THRESHOLD )); then
    LAST_RECOVERY_ACTION="transport_missing_debounce"
    return 1
  fi

  LAST_RECOVERY_ACTION="adb_reconnect_device"
  "$ADB" reconnect device >/dev/null 2>&1 || true
  if [[ "$(adb_state "$PD20_SERIAL_VALUE")" == "device" ]]; then
    reset_missing_count
    SELECTED_TARGET="$PD20_SERIAL_VALUE"
    SELECTED_TRANSPORT="usb"
    return 0
  fi

  if restart_adb_server_if_allowed && [[ "$(adb_state "$PD20_SERIAL_VALUE")" == "device" ]]; then
    reset_missing_count
    SELECTED_TARGET="$PD20_SERIAL_VALUE"
    SELECTED_TRANSPORT="usb"
    return 0
  fi
  return 1
}

valid_wireless_endpoint() {
  local endpoint="$1"
  local port=""
  [[ "$endpoint" =~ ^[A-Za-z0-9._-]+:([0-9]{1,5})$ ]] || return 1
  port="${BASH_REMATCH[1]}"
  (( port >= 1 && port <= 65535 ))
}

try_wireless_endpoint() {
  local endpoint="$1"
  local serial=""
  local model=""
  valid_wireless_endpoint "$endpoint" || return 1
  "$ADB" connect "$endpoint" >/dev/null 2>&1 || return 1
  [[ "$(adb_state "$endpoint")" == "device" ]] || return 1
  serial="$("$ADB" -s "$endpoint" shell getprop ro.serialno 2>/dev/null | tr -d '\r\n' || true)"
  [[ "$serial" == "$PD20_SERIAL_VALUE" ]] || return 1
  model="$("$ADB" -s "$endpoint" shell getprop ro.product.model 2>/dev/null | tr -d '\r\n' || true)"
  [[ "$model" == "$PD20_MODEL_VALUE" ]] || return 1

  write_atomic "$WIRELESS_ENDPOINT_FILE" "$endpoint"
  reset_missing_count
  SELECTED_TARGET="$endpoint"
  SELECTED_TRANSPORT="wireless"
  LAST_RECOVERY_ACTION="wireless_connected"
  return 0
}

select_wireless_target() {
  [[ "$WIRELESS_ENABLED" == "true" ]] || return 1

  local cached=""
  local endpoint=""
  local endpoints=""
  [[ -s "$WIRELESS_ENDPOINT_FILE" ]] && cached="$(<"$WIRELESS_ENDPOINT_FILE")"
  if [[ -n "$cached" ]] && try_wireless_endpoint "$cached"; then
    return 0
  fi

  endpoints="$("$ADB" mdns services 2>/dev/null |
    awk '$2 ~ /_adb-tls-connect[.]_tcp[.]?/ {print $3}' || true)"
  for endpoint in $endpoints; do
    [[ "$endpoint" == "$cached" ]] && continue
    if try_wireless_endpoint "$endpoint"; then
      return 0
    fi
  done
  LAST_RECOVERY_ACTION="wireless_unavailable"
  return 1
}

ensure_reverse() {
  local target="$1"
  if ! "$ADB" -s "$target" reverse --list 2>/dev/null |
    grep -qE '(^|[[:space:]])tcp:4340[[:space:]]+tcp:4340$'; then
    "$ADB" -s "$target" reverse tcp:4340 tcp:4340 >/dev/null
    LAST_RECOVERY_ACTION="reverse_restored"
    log_event "restored PD20 reverse tcp:4340 target=$target"
  fi
}

probe_device_manager() {
  local target="$1"
  local status="000"
  status="$("$ADB" -s "$target" shell /system/bin/curl \
    --silent --output /dev/null --write-out '%{http_code}' \
    --max-time "$DEVICE_READY_TIMEOUT" "$DEVICE_READY_URL" 2>/dev/null |
    tr -d '\r\n' || true)"
  [[ "$status" =~ ^[1-5][0-9][0-9]$ ]] || status="000"
  DEVICE_HTTP_STATUS="$status"
  [[ "$status" != "000" ]]
}

ensure_pd20_link() {
  [[ -n "$ADB" && -x "$ADB" ]] || {
    update_link_state "adb_unavailable" "none" "none" "adb_missing"
    return 0
  }

  SELECTED_TARGET=""
  SELECTED_TRANSPORT="none"
  LAST_RECOVERY_ACTION="none"

  if ! select_usb_target; then
    if [[ "$LAST_RECOVERY_ACTION" != "transport_missing_debounce" ]]; then
      select_wireless_target || true
    fi
  fi
  if [[ -z "$SELECTED_TARGET" ]]; then
    update_link_state "transport_unavailable" "none" "none" "$LAST_RECOVERY_ACTION"
    return 0
  fi

  if ! ensure_reverse "$SELECTED_TARGET"; then
    update_link_state "reverse_failed" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "reverse_failed"
    return 0
  fi

  if probe_device_manager "$SELECTED_TARGET"; then
    if [[ "$DEVICE_HTTP_STATUS" == "200" ]]; then
      update_link_state "ready" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "$LAST_RECOVERY_ACTION"
    else
      update_link_state "manager_not_ready" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "http_$DEVICE_HTTP_STATUS"
    fi
    return 0
  fi

  LAST_RECOVERY_ACTION="reverse_recreated"
  "$ADB" -s "$SELECTED_TARGET" reverse --remove tcp:4340 >/dev/null 2>&1 || true
  "$ADB" -s "$SELECTED_TARGET" reverse tcp:4340 tcp:4340 >/dev/null 2>&1 || {
    update_link_state "reverse_failed" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "reverse_recreate_failed"
    return 0
  }
  if probe_device_manager "$SELECTED_TARGET"; then
    if [[ "$DEVICE_HTTP_STATUS" == "200" ]]; then
      update_link_state "ready" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "$LAST_RECOVERY_ACTION"
    else
      update_link_state "manager_not_ready" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "http_$DEVICE_HTTP_STATUS"
    fi
  else
    update_link_state "device_ready_failed" "$SELECTED_TARGET" "$SELECTED_TRANSPORT" "$LAST_RECOVERY_ACTION"
  fi
}

acquire_lock
ensure_pd20_link

now="$("$DATE_BIN" +%s)"
if [[ ! -s "$STARTED_FILE" ]]; then
  write_atomic "$STARTED_FILE" "$now"
  write_atomic "$FAILURE_FILE" "0"
  exit 0
fi
started="$(read_uint "$STARTED_FILE" "$now")"
if (( now - started < GRACE )); then
  exit 0
fi

"$ROOT/scripts/rotate-logs.sh" >/dev/null 2>&1 || true

if "$CURL_BIN" --fail --silent --max-time 5 http://127.0.0.1:4340/health >/dev/null &&
  "$CURL_BIN" --fail --silent --max-time 5 http://127.0.0.1:4340/ready >/dev/null; then
  write_atomic "$FAILURE_FILE" "0"
  exit 0
fi

failures="$(read_uint "$FAILURE_FILE" 0)"
failures=$((failures + 1))
write_atomic "$FAILURE_FILE" "$failures"
log_event "readiness failure $failures/$THRESHOLD"

if (( failures < THRESHOLD )); then
  exit 0
fi

for label in \
  ai.coreline.heybot.proxy-grok \
  ai.coreline.heybot.proxy-video \
  ai.coreline.heybot.proxy-codex \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-vision \
  ai.coreline.heybot.proxy-draw \
  ai.coreline.heybot.proxy-brush \
  ai.coreline.heybot.proxy-audio \
  ai.coreline.heybot.proxy-conversation \
  ai.coreline.heybot.proxy-manager; do
  "$LAUNCHCTL_BIN" kickstart -k "$DOMAIN/$label" || true
done
write_atomic "$FAILURE_FILE" "0"
log_event "restarted proxy services"
