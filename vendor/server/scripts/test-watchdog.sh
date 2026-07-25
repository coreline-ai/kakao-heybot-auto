#!/usr/bin/env bash
# Hermetic regression test for scripts/watchdog.sh.  It uses only a temporary
# server root and fake command binaries; no live ADB, launchd, proxy, or secret
# is read or changed.
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
case "$*" in
  *" get-state")
    printf '%s\n' "${FAKE_ADB_STATE:-device}"
    ;;
  *" reverse --list")
    if [[ -f "$state_dir/reverse-present" ]]; then
      printf '%s\n' 'UsbFfs tcp:4340 tcp:4340'
    fi
    ;;
  *" reverse tcp:4340 tcp:4340")
    touch "$state_dir/reverse-present"
    ;;
  *" forward tcp:3000 tcp:3000")
    ;;
  *)
    printf 'unexpected fake adb command: %s\n' "$*" >&2
    exit 64
    ;;
esac
ADB

cat >"$FAKE_BIN/curl" <<'CURL'
#!/usr/bin/env bash
set -euo pipefail
if [[ "${FAKE_CURL_STATUS:-ok}" == "ok" ]]; then
  exit 0
fi
exit 22
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
  '-u +%FT%TZ') printf '2026-07-25T00:00:00Z\n' ;;
  '+%s') printf '%s\n' "${FAKE_NOW:?}" ;;
  *) printf 'unexpected fake date arguments: %s\n' "$*" >&2; exit 64 ;;
esac
DATE
chmod 700 "$FAKE_BIN/adb" "$FAKE_BIN/curl" "$FAKE_BIN/launchctl" "$FAKE_BIN/date"

run_watchdog() {
  env \
    HEYBOT_SERVER_ROOT="$TEST_ROOT" \
    ADB_BIN="$FAKE_BIN/adb" \
    CURL_BIN="$FAKE_BIN/curl" \
    LAUNCHCTL_BIN="$FAKE_BIN/launchctl" \
    DATE_BIN="$FAKE_BIN/date" \
    PD20_SERIAL="FAKE-PD20" \
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

# First run is inside startup grace.  A connected PD20 with a missing reverse
# must get only the required loopback mapping and no proxy restart.
run_watchdog FAKE_NOW=100 FAKE_ADB_STATE=device FAKE_CURL_STATUS=ok
[[ -f "$FAKE_STATE/reverse-present" ]] || { echo 'reverse was not restored' >&2; exit 1; }
assert_equals '100' "$(<"$TEST_ROOT/runtime/watchdog/started-at")" 'started timestamp'
assert_equals '0' "$(<"$TEST_ROOT/runtime/watchdog/consecutive-failures")" 'initial failure count'
assert_count 'kickstart' '0' "$FAKE_STATE/launchctl.log"

# A disconnected PD20 must not attempt reverse/forward operations.
rm -f "$FAKE_STATE/reverse-present" "$FAKE_STATE/adb.log"
run_watchdog FAKE_NOW=101 FAKE_ADB_STATE=offline FAKE_CURL_STATUS=ok
assert_count 'reverse tcp:4340 tcp:4340' '0' "$FAKE_STATE/adb.log"
assert_count 'forward tcp:3000 tcp:3000' '0' "$FAKE_STATE/adb.log"

# A healthy manager clears failures after grace without calling launchctl.
printf '0\n' >"$TEST_ROOT/runtime/watchdog/started-at"
printf '2\n' >"$TEST_ROOT/runtime/watchdog/consecutive-failures"
run_watchdog FAKE_NOW=200 FAKE_ADB_STATE=offline FAKE_CURL_STATUS=ok
assert_equals '0' "$(<"$TEST_ROOT/runtime/watchdog/consecutive-failures")" 'healthy reset'
assert_count 'kickstart' '0' "$FAKE_STATE/launchctl.log"

# Three consecutive readiness failures restart the fixed dependency order once.
run_watchdog FAKE_NOW=201 FAKE_ADB_STATE=offline FAKE_CURL_STATUS=fail
run_watchdog FAKE_NOW=202 FAKE_ADB_STATE=offline FAKE_CURL_STATUS=fail
assert_count 'kickstart' '0' "$FAKE_STATE/launchctl.log"
run_watchdog FAKE_NOW=203 FAKE_ADB_STATE=offline FAKE_CURL_STATUS=fail
assert_equals '0' "$(<"$TEST_ROOT/runtime/watchdog/consecutive-failures")" 'threshold reset'
assert_count 'kickstart' '3' "$FAKE_STATE/launchctl.log"
assert_equals 'kickstart -k gui/'"$(id -u)"'/ai.coreline.heybot.proxy-codex' "$(sed -n '1p' "$FAKE_STATE/launchctl.log")" 'restart codex first'
assert_equals 'kickstart -k gui/'"$(id -u)"'/ai.coreline.heybot.proxy-image' "$(sed -n '2p' "$FAKE_STATE/launchctl.log")" 'restart image second'
assert_equals 'kickstart -k gui/'"$(id -u)"'/ai.coreline.heybot.proxy-manager' "$(sed -n '3p' "$FAKE_STATE/launchctl.log")" 'restart manager third'

# Reverse can be restored on a later connected watchdog interval as well.
run_watchdog FAKE_NOW=204 FAKE_ADB_STATE=device FAKE_CURL_STATUS=ok
[[ -f "$FAKE_STATE/reverse-present" ]] || { echo 'later reverse recovery failed' >&2; exit 1; }

printf '%s\n' 'watchdog fake-ADB regression checks passed.'
