#!/usr/bin/env bash
set -euo pipefail

ROOT="${HEYBOT_SERVER_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
STATE_DIR="$ROOT/runtime/watchdog"
STARTED_FILE="$STATE_DIR/started-at"
FAILURE_FILE="$STATE_DIR/consecutive-failures"
THRESHOLD="${WATCHDOG_FAILURE_THRESHOLD:-3}"
GRACE="${WATCHDOG_STARTUP_GRACE_SECONDS:-60}"
DOMAIN="gui/$(id -u)"
CURL_BIN="${CURL_BIN:-curl}"
LAUNCHCTL_BIN="${LAUNCHCTL_BIN:-launchctl}"
DATE_BIN="${DATE_BIN:-date}"
mkdir -p "$STATE_DIR"
chmod 700 "$STATE_DIR"

ensure_adb_reverse() {
  local adb="${ADB_BIN:-}"
  local serial="${PD20_SERIAL:-0123456789ABCDEF}"
  [[ -n "$adb" && -x "$adb" ]] || return 0
  [[ "$("$adb" -s "$serial" get-state 2>/dev/null || true)" == "device" ]] || return 0
  if ! "$adb" -s "$serial" reverse --list 2>/dev/null |
    grep -qE '(^|[[:space:]])tcp:4340[[:space:]]+tcp:4340$'; then
    "$adb" -s "$serial" reverse tcp:4340 tcp:4340 >/dev/null
    printf '%s watchdog restored PD20 reverse tcp:4340\n' "$("$DATE_BIN" -u +%FT%TZ)"
  fi
}

ensure_adb_reverse

now="$("$DATE_BIN" +%s)"
if [[ ! -s "$STARTED_FILE" ]]; then
  printf '%s\n' "$now" >"$STARTED_FILE"
  printf '0\n' >"$FAILURE_FILE"
  exit 0
fi
started="$(<"$STARTED_FILE")"
if (( now - started < GRACE )); then
  exit 0
fi

"$ROOT/scripts/rotate-logs.sh" >/dev/null 2>&1 || true

if "$CURL_BIN" --fail --silent --max-time 5 http://127.0.0.1:4340/health >/dev/null &&
  "$CURL_BIN" --fail --silent --max-time 5 http://127.0.0.1:4340/ready >/dev/null; then
  printf '0\n' >"$FAILURE_FILE"
  exit 0
fi

failures=0
[[ -s "$FAILURE_FILE" ]] && failures="$(<"$FAILURE_FILE")"
failures=$((failures + 1))
printf '%s\n' "$failures" >"$FAILURE_FILE"
printf '%s watchdog readiness failure %s/%s\n' "$("$DATE_BIN" -u +%FT%TZ)" "$failures" "$THRESHOLD"

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
  ai.coreline.heybot.proxy-conversation \
  ai.coreline.heybot.proxy-manager; do
  "$LAUNCHCTL_BIN" kickstart -k "$DOMAIN/$label" || true
done
printf '0\n' >"$FAILURE_FILE"
printf '%s watchdog restarted proxy services\n' "$("$DATE_BIN" -u +%FT%TZ)"
