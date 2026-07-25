#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DOMAIN="gui/$(id -u)"
DESTINATION="${HOME}/Library/LaunchAgents"
MIRROR_ROOT="${HEYBOT_LAUNCHD_ROOT:-${HOME}/Library/Application Support/HeyBotProxy}"
RENDER_ONLY=false
if [[ "${1:-}" == "--render-only" ]]; then
  RENDER_ONLY=true
  DESTINATION="$ROOT/runtime/launchd"
fi

NODE_BIN="$(command -v node)"
CODEX_BIN="$(command -v codex)"
ADB_BIN="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
PATH_VALUE="$(dirname "$NODE_BIN"):$(dirname "$CODEX_BIN"):$(dirname "$ADB_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

if [[ "$RENDER_ONLY" == "false" ]]; then
  for label in \
    ai.coreline.heybot.proxy-watchdog \
    ai.coreline.heybot.proxy-manager \
    ai.coreline.heybot.proxy-image \
    ai.coreline.heybot.proxy-codex; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  done
fi

"$ROOT/scripts/bootstrap-secrets.sh"
for package in proxy-codex proxy-image proxy-manager; do
  (cd "$ROOT/$package" && npm run build >/dev/null)
  mkdir -p "$ROOT/$package/runtime/logs" "$ROOT/$package/runtime/state"
done
mkdir -p "$DESTINATION" "$ROOT/runtime/watchdog" "$MIRROR_ROOT/scripts"
chmod 700 "$ROOT/runtime" "$ROOT/runtime/watchdog" "$MIRROR_ROOT"

sync_package() {
  local id="$1"
  local source="$ROOT/proxy-$id"
  local target="$MIRROR_ROOT/proxy-$id"
  mkdir -p "$target" "$target/runtime"
  /usr/bin/rsync -a --delete "$source/dist/" "$target/dist/"
  if [[ -d "$source/config" ]]; then
    /usr/bin/rsync -a --delete "$source/config/" "$target/config/"
  else
    mkdir -p "$target/config"
  fi
  /usr/bin/rsync -a --delete "$source/node_modules/" "$target/node_modules/"
  cp "$source/package.json" "$source/package-lock.json" "$target/"

  # First install migrates the current durable queue/artifacts. Re-installs
  # preserve the internal runtime and only synchronize credentials.
  if [[ ! -e "$target/runtime/db/jobs.sqlite3" ]]; then
    /usr/bin/rsync -a "$source/runtime/" "$target/runtime/"
  fi
  mkdir -p "$target/runtime/logs" "$target/runtime/state" "$target/runtime/secrets"
  /usr/bin/rsync -a --delete "$source/runtime/secrets/" "$target/runtime/secrets/"
  chmod 700 "$target" "$target/runtime" "$target/runtime/secrets"
  find "$target/runtime/secrets" -type f -exec chmod 600 {} +
}

for id in codex image manager; do
  sync_package "$id"
done
cp \
  "$ROOT/scripts/watchdog.sh" \
  "$ROOT/scripts/rotate-logs.sh" \
  "$ROOT/scripts/retention-cleanup.sh" \
  "$MIRROR_ROOT/scripts/"
chmod 700 "$MIRROR_ROOT/scripts/"*.sh
mkdir -p "$MIRROR_ROOT/runtime/watchdog"
chmod 700 "$MIRROR_ROOT/runtime" "$MIRROR_ROOT/runtime/watchdog"

xml_escape() {
  printf '%s' "$1" |
    sed -e 's/&/\\&amp;/g' -e 's/</\\&lt;/g' -e 's/>/\\&gt;/g' \
      -e 's/\"/\\&quot;/g' -e "s/'/\\&apos;/g"
}

write_proxy_plist() {
  local id="$1"
  local label="ai.coreline.heybot.proxy-${id}"
  local package="$MIRROR_ROOT/proxy-${id}"
  local output="$DESTINATION/${label}.plist"
  local extra_environment=""
  if [[ "$id" == "codex" ]]; then
    extra_environment="
      <key>CODEX_CLI_BIN</key>
      <string>$(xml_escape "$CODEX_BIN")</string>"
  fi
  cat >"$output" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$NODE_BIN")</string>
    <string>$(xml_escape "$package/dist/src/index.js")</string>
  </array>
  <key>WorkingDirectory</key>
  <string>$(xml_escape "$package")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$(xml_escape "$HOME")</string>
    <key>PATH</key>
    <string>$(xml_escape "$PATH_VALUE")</string>${extra_environment}
  </dict>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <dict>
    <key>SuccessfulExit</key>
    <false/>
  </dict>
  <key>ProcessType</key>
  <string>Background</string>
  <key>ThrottleInterval</key>
  <integer>10</integer>
  <key>Umask</key>
  <integer>63</integer>
  <key>StandardOutPath</key>
  <string>$(xml_escape "$package/runtime/logs/launchd.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$package/runtime/logs/launchd-error.log")</string>
</dict>
</plist>
EOF
  plutil -lint "$output" >/dev/null
}

write_watchdog_plist() {
  local label="ai.coreline.heybot.proxy-watchdog"
  local output="$DESTINATION/${label}.plist"
  cat >"$output" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/bash</string>
    <string>$(xml_escape "$MIRROR_ROOT/scripts/watchdog.sh")</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict>
    <key>HOME</key>
    <string>$(xml_escape "$HOME")</string>
    <key>PATH</key>
    <string>$(xml_escape "$PATH_VALUE")</string>
    <key>WATCHDOG_FAILURE_THRESHOLD</key>
    <string>3</string>
    <key>WATCHDOG_STARTUP_GRACE_SECONDS</key>
    <string>60</string>
    <key>ADB_BIN</key>
    <string>$(xml_escape "$ADB_BIN")</string>
    <key>PD20_SERIAL</key>
    <string>0123456789ABCDEF</string>
  </dict>
  <key>StartInterval</key>
  <integer>30</integer>
  <key>ProcessType</key>
  <string>Background</string>
  <key>StandardOutPath</key>
  <string>$(xml_escape "$MIRROR_ROOT/runtime/watchdog/watchdog.log")</string>
  <key>StandardErrorPath</key>
  <string>$(xml_escape "$MIRROR_ROOT/runtime/watchdog/watchdog-error.log")</string>
</dict>
</plist>
EOF
  plutil -lint "$output" >/dev/null
}

for id in codex image manager; do
  write_proxy_plist "$id"
done
write_watchdog_plist

if [[ "$RENDER_ONLY" == "true" ]]; then
  printf 'launchd plists rendered and validated in %s\n' "$DESTINATION"
  exit 0
fi

for label in \
  ai.coreline.heybot.proxy-codex \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-manager \
  ai.coreline.heybot.proxy-watchdog; do
  launchctl bootstrap "$DOMAIN" "$DESTINATION/$label.plist"
done

for label in \
  ai.coreline.heybot.proxy-codex \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-manager; do
  launchctl kickstart -k "$DOMAIN/$label"
done

printf 'Independent proxy launchd services installed from mirror: %s\n' "$MIRROR_ROOT"
