#!/usr/bin/env bash
# Read-only Phase 6 soak: proxy readiness + PD20 process/reverse continuity.
set -euo pipefail

ADB="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
SERIAL="${SERIAL:-0123456789ABCDEF}"
DURATION_SECONDS="${DURATION_SECONDS:-1800}"
INTERVAL_SECONDS="${INTERVAL_SECONDS:-5}"
DEVICE_SAMPLE_EVERY="${DEVICE_SAMPLE_EVERY:-6}"
PROBE_ATTEMPTS="${PROBE_ATTEMPTS:-3}"
PROBE_RETRY_DELAY_SECONDS="${PROBE_RETRY_DELAY_SECONDS:-2}"

[[ "$DURATION_SECONDS" =~ ^[0-9]+$ && "$DURATION_SECONDS" -ge 1 ]] || {
  printf 'ERROR: DURATION_SECONDS must be a positive integer\n' >&2
  exit 2
}
[[ "$INTERVAL_SECONDS" =~ ^[0-9]+$ && "$INTERVAL_SECONDS" -ge 1 ]] || {
  printf 'ERROR: INTERVAL_SECONDS must be a positive integer\n' >&2
  exit 2
}
for value_name in DEVICE_SAMPLE_EVERY PROBE_ATTEMPTS PROBE_RETRY_DELAY_SECONDS; do
  value="${!value_name}"
  [[ "$value" =~ ^[0-9]+$ && "$value" -ge 1 ]] || {
    printf 'ERROR: %s must be a positive integer\n' "$value_name" >&2
    exit 2
  }
done
[[ -x "$ADB" ]] || { printf 'ERROR: adb not found: %s\n' "$ADB" >&2; exit 1; }

probe_body="$(mktemp -t heybot-audio-soak.XXXXXX)"
trap 'rm -f "$probe_body"' EXIT
transient_recoveries=0

probe() {
  local name="$1"
  local url="$2"
  local attempt code="000"
  for ((attempt = 1; attempt <= PROBE_ATTEMPTS; attempt++)); do
    if code="$(curl --silent --show-error --output "$probe_body" --write-out '%{http_code}' --max-time 20 "$url")" &&
      [[ "$code" == "200" ]]; then
      if ((attempt > 1)); then
        transient_recoveries=$((transient_recoveries + 1))
        printf 'WARN: %s recovered on attempt %d/%d\n' "$name" "$attempt" "$PROBE_ATTEMPTS" >&2
      fi
      return 0
    fi
    if ((attempt < PROBE_ATTEMPTS)); then
      sleep "$PROBE_RETRY_DELAY_SECONDS"
    fi
  done
  printf 'ERROR: %s probe failed after %d attempts (HTTP %s): ' "$name" "$PROBE_ATTEMPTS" "$code" >&2
  tr '\r\n' ' ' < "$probe_body" | cut -c1-600 >&2
  printf '\n' >&2
  return 1
}

started="$(date +%s)"
deadline=$((started + DURATION_SECONDS))
samples=0
device_samples=0

while (( $(date +%s) < deadline )); do
  probe manager-health http://127.0.0.1:4340/health
  probe manager-ready http://127.0.0.1:4340/ready
  probe audio-ready http://127.0.0.1:4363/ready
  probe conversation-ready http://127.0.0.1:4361/ready
  samples=$((samples + 1))

  if (( samples == 1 || samples % DEVICE_SAMPLE_EVERY == 0 )); then
    "$ADB" -s "$SERIAL" get-state | grep -qx device
    "$ADB" -s "$SERIAL" reverse --list | grep -Eq 'tcp:4340[[:space:]]+tcp:4340'
    "$ADB" -s "$SERIAL" shell "su root sh -c 'pgrep -f \"[a]i.coreline.heybot\" >/dev/null'"
    device_samples=$((device_samples + 1))
  fi
  sleep "$INTERVAL_SECONDS"
done

finished="$(date +%s)"
printf '{"status":"passed","durationSeconds":%d,"samples":%d,"deviceSamples":%d,"transientRecoveries":%d,"serial":"%s"}\n' \
  "$((finished - started))" "$samples" "$device_samples" "$transient_recoveries" "$SERIAL"
