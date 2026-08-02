#!/usr/bin/env bash
# Hermetic regression for scripts/watchdog.sh. Only temporary fake commands
# and state are used; live ADB, USB, launchd, proxies, and secrets are untouched.
set -euo pipefail

SOURCE_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/heybot-watchdog-test.XXXXXX")"
TEST_ROOT="$TEMP_ROOT/server"
FAKE_BIN="$TEMP_ROOT/bin"
FAKE_STATE="$TEMP_ROOT/state"
trap 'rm -rf "$TEMP_ROOT"' EXIT

mkdir -p "$TEST_ROOT/scripts" "$TEST_ROOT/runtime/watchdog" "$FAKE_BIN" "$FAKE_STATE"
cp "$SOURCE_ROOT/scripts/watchdog.sh" "$TEST_ROOT/scripts/watchdog.sh"
cat >"$TEST_ROOT/scripts/rotate-logs.sh" <<'ROTATE'
#!/usr/bin/env bash
exit 0
ROTATE
chmod 700 "$TEST_ROOT/scripts/watchdog.sh" "$TEST_ROOT/scripts/rotate-logs.sh"

cat >"$FAKE_BIN/adb" <<'ADB'
#!/usr/bin/env bash
set -euo pipefail
state_dir="${FAKE_STATE_DIR:?}"
log="$state_dir/adb.log"
printf '%s\n' "$*" >>"$log"

target_key() {
  printf '%s' "$1" | tr -cd 'A-Za-z0-9._-'
}

target_state() {
  local target="$1"
  local key
  key="$(target_key "$target")"
  if [[ -s "$state_dir/state-$key" ]]; then
    cat "$state_dir/state-$key"
  elif [[ "$target" == "${PD20_SERIAL:-FAKE-PD20}" ]]; then
    printf '%s\n' "${FAKE_ADB_STATE:-device}"
  else
    printf '%s\n' "${FAKE_WIRELESS_STATE:-absent}"
  fi
}

set_target_state() {
  local target="$1"
  local value="$2"
  printf '%s\n' "$value" >"$state_dir/state-$(target_key "$target")"
}

case "$*" in
  "-s "*" get-state")
    target="$2"
    state="$(target_state "$target")"
    [[ "$state" != "absent" ]] || exit 1
    printf '%s\n' "$state"
    ;;
  "-s "*" reconnect")
    target="$2"
    set_target_state "$target" "${FAKE_ADB_STATE_AFTER_TARGET_RECONNECT:-device}"
    ;;
  "reconnect device")
    set_target_state "${PD20_SERIAL:-FAKE-PD20}" "${FAKE_ADB_STATE_AFTER_RECONNECT:-absent}"
    ;;
  "kill-server")
    :
    ;;
  "start-server")
    set_target_state "${PD20_SERIAL:-FAKE-PD20}" "${FAKE_ADB_STATE_AFTER_RESTART:-device}"
    ;;
  "-s "*" reverse --list")
    target="$2"
    key="$(target_key "$target")"
    [[ -f "$state_dir/reverse-$key" ]] && printf '%s\n' 'UsbFfs tcp:4340 tcp:4340'
    ;;
  "-s "*" reverse tcp:4340 tcp:4340")
    target="$2"
    touch "$state_dir/reverse-$(target_key "$target")"
    ;;
  "-s "*" reverse --remove tcp:4340")
    target="$2"
    rm -f "$state_dir/reverse-$(target_key "$target")"
    ;;
  "-s "*" shell /system/bin/curl "*)
    count=0
    [[ -s "$state_dir/device-ready-count" ]] && count="$(<"$state_dir/device-ready-count")"
    count=$((count + 1))
    printf '%s\n' "$count" >"$state_dir/device-ready-count"
    if [[ "${FAKE_DEVICE_READY_SEQUENCE:-}" == "fail,ok" ]]; then
      if [[ "$count" -ge 2 ]]; then printf '200'; exit 0; fi
      printf '000'
      exit 7
    fi
    case "${FAKE_DEVICE_READY:-ok}" in
      ok) printf '200' ;;
      unavailable) printf '503' ;;
      *) printf '000'; exit 7 ;;
    esac
    ;;
  "-s "*" shell getprop ro.serialno")
    target="$2"
    if [[ "$target" == "${PD20_SERIAL:-FAKE-PD20}" ]]; then
      printf '%s\n' "${PD20_SERIAL:-FAKE-PD20}"
    else
      printf '%s\n' "${FAKE_WIRELESS_SERIAL:-${PD20_SERIAL:-FAKE-PD20}}"
    fi
    ;;
  "-s "*" shell getprop ro.product.model")
    target="$2"
    if [[ "$target" == "${PD20_SERIAL:-FAKE-PD20}" ]]; then
      printf '%s\n' "${PD20_MODEL:-PD20}"
    else
      printf '%s\n' "${FAKE_WIRELESS_MODEL:-${PD20_MODEL:-PD20}}"
    fi
    ;;
  "mdns services")
    printf '%s\n' 'List of discovered mdns services'
    if [[ -n "${FAKE_MDNS_ENDPOINT:-}" ]]; then
      printf 'fake-adb\t_adb-tls-connect._tcp.\t%s\n' "$FAKE_MDNS_ENDPOINT"
    fi
    ;;
  "connect "*)
    endpoint="$2"
    [[ "$endpoint" != "${FAKE_CONNECT_FAIL_ENDPOINT:-}" ]] || exit 1
    set_target_state "$endpoint" "${FAKE_WIRELESS_STATE_AFTER_CONNECT:-device}"
    printf 'connected to %s\n' "$endpoint"
    ;;
  *)
    printf 'unexpected fake adb command: %s\n' "$*" >&2
    exit 64
    ;;
esac
ADB

cat >"$FAKE_BIN/ioreg" <<'IOREG'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${FAKE_STATE_DIR:?}/ioreg.log"
if [[ "${FAKE_USB_PRESENT:-yes}" == "yes" ]]; then
  printf '    "USB Serial Number" = "%s"\n' "${PD20_SERIAL:-FAKE-PD20}"
fi
IOREG

cat >"$FAKE_BIN/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
[[ "${FAKE_CURL_STATUS:-ok}" == "ok" ]]
CURL

cat >"$FAKE_BIN/launchctl" <<'LAUNCHCTL'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"${FAKE_STATE_DIR:?}/launchctl.log"
LAUNCHCTL

cat >"$FAKE_BIN/date" <<'DATE'
#!/usr/bin/env bash
set -euo pipefail
case "$*" in
  '-u +%FT%TZ') printf '2026-08-02T10:00:00Z\n' ;;
  '+%s') printf '%s\n' "${FAKE_NOW:?}" ;;
  *) printf 'unexpected fake date arguments: %s\n' "$*" >&2; exit 64 ;;
esac
DATE

cat >"$FAKE_BIN/sleep" <<'SLEEP'
#!/usr/bin/env bash
exit 0
SLEEP
chmod 700 "$FAKE_BIN/adb" "$FAKE_BIN/ioreg" "$FAKE_BIN/curl" \
  "$FAKE_BIN/launchctl" "$FAKE_BIN/date" "$FAKE_BIN/sleep"

run_watchdog() {
  env \
    HEYBOT_SERVER_ROOT="$TEST_ROOT" \
    ADB_BIN="$FAKE_BIN/adb" \
    CURL_BIN="$FAKE_BIN/curl" \
    LAUNCHCTL_BIN="$FAKE_BIN/launchctl" \
    DATE_BIN="$FAKE_BIN/date" \
    IOREG_BIN="$FAKE_BIN/ioreg" \
    SLEEP_BIN="$FAKE_BIN/sleep" \
    PD20_SERIAL="FAKE-PD20" \
    PD20_MODEL="PD20" \
    PD20_LINK_MISSING_THRESHOLD="2" \
    PD20_ADB_RESTART_COOLDOWN_SECONDS="300" \
    WATCHDOG_STARTUP_GRACE_SECONDS="60" \
    WATCHDOG_FAILURE_THRESHOLD="3" \
    FAKE_STATE_DIR="$FAKE_STATE" \
    "$@" \
    "$TEST_ROOT/scripts/watchdog.sh"
}

assert_equals() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  [[ "$expected" == "$actual" ]] || {
    printf 'assertion failed (%s): expected=%q actual=%q\n' "$label" "$expected" "$actual" >&2
    exit 1
  }
}

assert_count() {
  local pattern="$1"
  local expected="$2"
  local file="$3"
  local actual=0
  [[ -f "$file" ]] && actual="$(grep -c -- "$pattern" "$file" || true)"
  assert_equals "$expected" "$actual" "count $pattern"
}

reset_link_fixture() {
  rm -f "$FAKE_STATE"/adb.log "$FAKE_STATE"/ioreg.log \
    "$FAKE_STATE"/state-* "$FAKE_STATE"/reverse-* \
    "$FAKE_STATE"/device-ready-count
  rm -f "$TEST_ROOT/runtime/watchdog"/transport-missing-count \
    "$TEST_ROOT/runtime/watchdog"/last-adb-restart-at \
    "$TEST_ROOT/runtime/watchdog"/wireless-endpoint \
    "$TEST_ROOT/runtime/watchdog"/active-target \
    "$TEST_ROOT/runtime/watchdog"/link-status \
    "$TEST_ROOT/runtime/watchdog"/connection-state.json
}

# Connected USB PD20 restores only reverse and passes device-side readiness.
run_watchdog FAKE_NOW=1000 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
[[ -f "$FAKE_STATE/reverse-FAKE-PD20" ]] || { echo 'reverse was not restored' >&2; exit 1; }
assert_equals 'ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'initial link status'
assert_equals '1000' "$(<"$TEST_ROOT/runtime/watchdog/started-at")" 'started timestamp'
assert_count 'kill-server' '0' "$FAKE_STATE/adb.log"
assert_count 'forward tcp:3000 tcp:3000' '0' "$FAKE_STATE/adb.log"
assert_count 'tcpip 5555' '0' "$FAKE_STATE/adb.log"

# One transient missing interval while USB is present is debounced.
reset_link_fixture
run_watchdog FAKE_NOW=1100 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=yes FAKE_CURL_STATUS=ok
assert_count 'kill-server' '0' "$FAKE_STATE/adb.log"
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"
assert_equals 'transport_unavailable' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'debounced link status'

# Second missing interval reconnects, then restarts ADB and restores reverse.
run_watchdog FAKE_NOW=1110 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=yes \
  FAKE_ADB_STATE_AFTER_RECONNECT=absent FAKE_ADB_STATE_AFTER_RESTART=device \
  FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count 'reconnect device' '1' "$FAKE_STATE/adb.log"
assert_count 'kill-server' '1' "$FAKE_STATE/adb.log"
assert_count 'start-server' '1' "$FAKE_STATE/adb.log"
assert_equals 'ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'restarted link status'

# Cooldown blocks another global ADB restart.
printf 'absent\n' >"$FAKE_STATE/state-FAKE-PD20"
printf '2\n' >"$TEST_ROOT/runtime/watchdog/transport-missing-count"
run_watchdog FAKE_NOW=1120 FAKE_USB_PRESENT=yes FAKE_ADB_STATE_AFTER_RECONNECT=absent FAKE_CURL_STATUS=ok
assert_count 'kill-server' '1' "$FAKE_STATE/adb.log"

# Offline target uses scoped reconnect without a global daemon restart.
reset_link_fixture
printf 'offline\n' >"$FAKE_STATE/state-FAKE-PD20"
run_watchdog FAKE_NOW=1500 FAKE_ADB_STATE_AFTER_TARGET_RECONNECT=device FAKE_USB_PRESENT=yes \
  FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count '-s FAKE-PD20 reconnect' '1' "$FAKE_STATE/adb.log"
assert_count 'kill-server' '0' "$FAKE_STATE/adb.log"
assert_equals 'ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'offline recovery'

# Unauthorized target is never restarted or granted automatically.
reset_link_fixture
printf 'unauthorized\n' >"$FAKE_STATE/state-FAKE-PD20"
run_watchdog FAKE_NOW=1600 FAKE_USB_PRESENT=yes FAKE_CURL_STATUS=ok
assert_count 'kill-server' '0' "$FAKE_STATE/adb.log"
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"

# A broken device-side readiness probe recreates reverse once and recovers.
reset_link_fixture
touch "$FAKE_STATE/reverse-FAKE-PD20"
run_watchdog FAKE_NOW=1700 FAKE_ADB_STATE=device FAKE_DEVICE_READY_SEQUENCE=fail,ok FAKE_CURL_STATUS=ok
assert_count 'reverse --remove tcp:4340' '1' "$FAKE_STATE/adb.log"
assert_count 'reverse tcp:4340 tcp:4340' '1' "$FAKE_STATE/adb.log"
assert_equals 'ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'ready after reverse recreation'

# HTTP 503 proves the transport is reachable; it must not churn reverse.
reset_link_fixture
touch "$FAKE_STATE/reverse-FAKE-PD20"
run_watchdog FAKE_NOW=1750 FAKE_ADB_STATE=device FAKE_DEVICE_READY=unavailable FAKE_CURL_STATUS=fail
assert_count 'reverse --remove tcp:4340' '0' "$FAKE_STATE/adb.log"
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"
assert_equals 'manager_not_ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'manager unavailable classification'

# Physically absent USB can use only an explicitly enabled, paired TLS endpoint.
reset_link_fixture
run_watchdog FAKE_NOW=1800 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=no \
  PD20_WIRELESS_ADB_ENABLED=true FAKE_MDNS_ENDPOINT=192.0.2.20:37111 \
  FAKE_WIRELESS_SERIAL=FAKE-PD20 FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count 'mdns services' '1' "$FAKE_STATE/adb.log"
assert_count 'connect 192.0.2.20:37111' '1' "$FAKE_STATE/adb.log"
assert_equals '192.0.2.20:37111' "$(<"$TEST_ROOT/runtime/watchdog/active-target")" 'wireless target'
assert_equals 'ready' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'wireless ready'

# A stale cached TLS port falls back to current mDNS discovery without a loop.
rm -f "$FAKE_STATE/adb.log" "$FAKE_STATE"/state-* "$FAKE_STATE"/reverse-* \
  "$FAKE_STATE/device-ready-count"
run_watchdog FAKE_NOW=1850 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=no \
  PD20_WIRELESS_ADB_ENABLED=true FAKE_CONNECT_FAIL_ENDPOINT=192.0.2.20:37111 \
  FAKE_MDNS_ENDPOINT=192.0.2.20:38444 FAKE_WIRELESS_SERIAL=FAKE-PD20 \
  FAKE_WIRELESS_MODEL=PD20 FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count 'connect 192.0.2.20:37111' '1' "$FAKE_STATE/adb.log"
assert_count 'connect 192.0.2.20:38444' '1' "$FAKE_STATE/adb.log"
assert_equals '192.0.2.20:38444' "$(<"$TEST_ROOT/runtime/watchdog/wireless-endpoint")" 'rediscovered wireless port'

# USB always regains priority on the next cycle even when wireless is cached.
rm -f "$FAKE_STATE/adb.log" "$FAKE_STATE"/state-* "$FAKE_STATE"/reverse-* \
  "$FAKE_STATE/device-ready-count"
run_watchdog FAKE_NOW=1860 FAKE_ADB_STATE=device FAKE_USB_PRESENT=yes \
  PD20_WIRELESS_ADB_ENABLED=true FAKE_MDNS_ENDPOINT=192.0.2.20:38444 \
  FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count 'mdns services' '0' "$FAKE_STATE/adb.log"
assert_count 'connect ' '0' "$FAKE_STATE/adb.log"
assert_equals 'FAKE-PD20' "$(<"$TEST_ROOT/runtime/watchdog/active-target")" 'USB priority target'

# A discovered endpoint for another Android device is rejected.
reset_link_fixture
run_watchdog FAKE_NOW=1900 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=no \
  PD20_WIRELESS_ADB_ENABLED=true FAKE_MDNS_ENDPOINT=192.0.2.99:37112 \
  FAKE_WIRELESS_SERIAL=OTHER-SERIAL FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_equals 'transport_unavailable' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'wrong wireless serial'
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"

# A matching serial with a different model is also rejected.
reset_link_fixture
run_watchdog FAKE_NOW=1950 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=no \
  PD20_WIRELESS_ADB_ENABLED=true FAKE_MDNS_ENDPOINT=192.0.2.99:37113 \
  FAKE_WIRELESS_SERIAL=FAKE-PD20 FAKE_WIRELESS_MODEL=OTHER-MODEL \
  FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_equals 'transport_unavailable' "$(<"$TEST_ROOT/runtime/watchdog/link-status")" 'wrong wireless model'
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"

# Wireless discovery is never attempted without explicit opt-in.
reset_link_fixture
run_watchdog FAKE_NOW=2000 FAKE_ADB_STATE=absent FAKE_USB_PRESENT=no \
  PD20_WIRELESS_ADB_ENABLED=false FAKE_MDNS_ENDPOINT=192.0.2.20:37111 FAKE_CURL_STATUS=ok
assert_count 'mdns services' '0' "$FAKE_STATE/adb.log"
assert_count 'connect ' '0' "$FAKE_STATE/adb.log"

# An overlapping launchd invocation exits before touching ADB.
reset_link_fixture
mkdir -p "$TEST_ROOT/runtime/watchdog/run.lock"
printf '%s\n' "$$" >"$TEST_ROOT/runtime/watchdog/run.lock/pid"
run_watchdog FAKE_NOW=2050 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_count 'get-state' '0' "$FAKE_STATE/adb.log"
rm -f "$TEST_ROOT/runtime/watchdog/run.lock/pid"
rmdir "$TEST_ROOT/runtime/watchdog/run.lock"

# Healthy manager clears failures; three host readiness failures restart all
# nine fixed service labels in dependency order.
printf '0\n' >"$TEST_ROOT/runtime/watchdog/started-at"
printf '2\n' >"$TEST_ROOT/runtime/watchdog/consecutive-failures"
reset_link_fixture
run_watchdog FAKE_NOW=2100 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=ok
assert_equals '0' "$(<"$TEST_ROOT/runtime/watchdog/consecutive-failures")" 'healthy reset'
assert_count 'kickstart' '0' "$FAKE_STATE/launchctl.log"

run_watchdog FAKE_NOW=2201 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=fail
run_watchdog FAKE_NOW=2202 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=fail
assert_count 'kickstart' '0' "$FAKE_STATE/launchctl.log"
run_watchdog FAKE_NOW=2203 FAKE_ADB_STATE=device FAKE_DEVICE_READY=ok FAKE_CURL_STATUS=fail
assert_equals '0' "$(<"$TEST_ROOT/runtime/watchdog/consecutive-failures")" 'threshold reset'
assert_count 'kickstart' '9' "$FAKE_STATE/launchctl.log"
assert_equals 'kickstart -k gui/'"$(id -u)"'/ai.coreline.heybot.proxy-grok' "$(sed -n '1p' "$FAKE_STATE/launchctl.log")" 'restart grok first'
assert_equals 'kickstart -k gui/'"$(id -u)"'/ai.coreline.heybot.proxy-manager' "$(sed -n '9p' "$FAKE_STATE/launchctl.log")" 'restart manager last'

printf '%s\n' 'watchdog transport self-heal regression checks passed.'
