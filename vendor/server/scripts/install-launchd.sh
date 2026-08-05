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
FFPROBE_BIN="$(command -v ffprobe)"
FFMPEG_BIN="$(command -v ffmpeg)"
YTDLP_BIN="${YOUTUBE_PROXY_YTDLP_BIN:-$ROOT/proxy-youtube/runtime/yt-dlp-venv/bin/yt-dlp}"
WHISPER_BIN="${AUDIO_PROXY_WHISPER_BIN:-$(command -v whisper-cli || true)}"
[[ -n "$WHISPER_BIN" ]] || WHISPER_BIN="whisper-cli"
WHISPER_MODEL_SHA256="${AUDIO_PROXY_WHISPER_MODEL_SHA256:-}"
ADB_BIN="${ADB:-$HOME/Library/Android/sdk/platform-tools/adb}"
GROK_CLI_COMMAND="${GROK_PROXY_CLI_COMMAND:-$HOME/.grok/bin/grok}"
GROK_CLI_HOME="${GROK_PROXY_CLI_HOME:-$HOME}"
GROK_SESSION_ROOT="${GROK_PROXY_SESSION_ROOT:-$HOME/.grok/sessions}"
PD20_WIRELESS_ADB_ENABLED="${PD20_WIRELESS_ADB_ENABLED:-false}"
PATH_VALUE="$(dirname "$NODE_BIN"):$(dirname "$CODEX_BIN"):$(dirname "$ADB_BIN"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"

[[ "$PD20_WIRELESS_ADB_ENABLED" == "true" || "$PD20_WIRELESS_ADB_ENABLED" == "false" ]] || {
  printf '%s\n' 'PD20_WIRELESS_ADB_ENABLED must be true or false.' >&2
  exit 1
}

[[ -x "$GROK_CLI_COMMAND" ]] || { printf '%s\n' "Grok CLI is not executable: $GROK_CLI_COMMAND" >&2; exit 1; }
[[ -d "$GROK_CLI_HOME" && -d "$GROK_SESSION_ROOT" ]] || {
  printf '%s\n' 'Grok CLI home/session root is not ready.' >&2
  exit 1
}

if [[ "$RENDER_ONLY" == "false" ]]; then
  for label in \
    ai.coreline.heybot.proxy-watchdog \
    ai.coreline.heybot.proxy-manager \
    ai.coreline.heybot.proxy-image \
    ai.coreline.heybot.proxy-vision \
    ai.coreline.heybot.proxy-codex \
    ai.coreline.heybot.proxy-video \
    ai.coreline.heybot.proxy-grok \
    ai.coreline.heybot.proxy-draw \
    ai.coreline.heybot.proxy-brush \
    ai.coreline.heybot.proxy-audio \
    ai.coreline.heybot.proxy-youtube \
    ai.coreline.heybot.proxy-conversation; do
    launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  done
fi

"$ROOT/scripts/bootstrap-secrets.sh"
for package in proxy-codex proxy-image proxy-vision proxy-grok proxy-video proxy-draw proxy-manager proxy-conversation proxy-audio proxy-youtube; do
  (cd "$ROOT/$package" && npm run build >/dev/null)
  mkdir -p "$ROOT/$package/runtime/logs" "$ROOT/$package/runtime/state"
done
if ! "$ROOT/proxy-brush/scripts/doctor.sh" >/dev/null; then
  printf '%s\n' "proxy-brush runtime is not ready. Run proxy-brush/scripts/bootstrap-engine.sh before installing launchd." >&2
  exit 1
fi
if ! GROK_PROXY_CLI_COMMAND="$GROK_CLI_COMMAND" \
  GROK_PROXY_CLI_HOME="$GROK_CLI_HOME" \
  GROK_PROXY_SESSION_ROOT="$GROK_SESSION_ROOT" \
  "$ROOT/proxy-grok/scripts/doctor.sh" >/dev/null; then
  printf '%s\n' 'proxy-grok runtime is not ready. Complete the local Grok CLI login first.' >&2
  exit 1
fi
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

  # First install migrates existing generation queues. Audio transcripts are
  # sensitive and must never be copied from a development runtime into the
  # launchd mirror; only its explicitly provisioned model is synchronized.
  if [[ "$id" == "audio" ]]; then
    mkdir -p "$target/runtime/db" "$target/runtime/jobs" "$target/runtime/models"
    if [[ -d "$source/runtime/models" ]]; then
      /usr/bin/rsync -a "$source/runtime/models/" "$target/runtime/models/"
    fi
  elif [[ ! -e "$target/runtime/db/jobs.sqlite3" ]]; then
    /usr/bin/rsync -a "$source/runtime/" "$target/runtime/"
  fi
  mkdir -p "$target/runtime/logs" "$target/runtime/state" "$target/runtime/secrets"
  /usr/bin/rsync -a --delete "$source/runtime/secrets/" "$target/runtime/secrets/"
  chmod 700 "$target" "$target/runtime" "$target/runtime/secrets"
  find "$target/runtime/secrets" -type f -exec chmod 600 {} +
}

for id in codex image vision grok video draw manager conversation audio youtube; do
  sync_package "$id"
done

sync_brush_package() {
  local source="$ROOT/proxy-brush"
  local target="$MIRROR_ROOT/proxy-brush"
  mkdir -p "$target/runtime"
  /usr/bin/rsync -a --delete "$source/src/" "$target/src/"
  /usr/bin/rsync -a --delete "$source/scripts/" "$target/scripts/"
  /usr/bin/rsync -a --delete "$source/engine/" "$target/engine/"
  /usr/bin/rsync -a --delete "$source/runtime/python-venv/" "$target/runtime/python-venv/"
  /usr/bin/rsync -a --delete "$source/runtime/remotion-browser/" "$target/runtime/remotion-browser/"
  cp "$source/package.json" "$source/.env.example" "$source/requirements-core.lock" \
    "$source/engine-manifest.sha256" "$target/"
  mkdir -p "$target/runtime/jobs" "$target/runtime/logs" "$target/runtime/models" "$target/runtime/secrets"
  /usr/bin/rsync -a --delete "$source/runtime/secrets/" "$target/runtime/secrets/"
  # browser.json contains an absolute executable path, so regenerate it after
  # copying the runtime into the isolated launchd mirror.
  node "$target/scripts/ensure-remotion-browser.mjs" >/dev/null
  chmod 700 "$target" "$target/runtime" "$target/runtime/secrets"
  find "$target/runtime/secrets" -type f -exec chmod 600 {} +
}

sync_brush_package
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
  local entry="$package/dist/src/index.js"
  if [[ "$id" == "codex" ]]; then
    extra_environment="
      <key>CODEX_CLI_BIN</key>
      <string>$(xml_escape "$CODEX_BIN")</string>"
  elif [[ "$id" == "brush" ]]; then
    entry="$package/src/index.mjs"
    extra_environment="
      <key>PEN_BRUSH_PROXY_ENABLED</key>
      <string>true</string>"
  elif [[ "$id" == "grok" ]]; then
    extra_environment="
      <key>GROK_PROXY_CLI_COMMAND</key>
      <string>$(xml_escape "$GROK_CLI_COMMAND")</string>
      <key>GROK_PROXY_CLI_HOME</key>
      <string>$(xml_escape "$GROK_CLI_HOME")</string>
      <key>GROK_PROXY_SESSION_ROOT</key>
      <string>$(xml_escape "$GROK_SESSION_ROOT")</string>
      <key>GROK_PROXY_CONVERSATION_SECRET_FILE</key>
      <string>$(xml_escape "$MIRROR_ROOT/proxy-grok/runtime/secrets/grok-conversation.secret")</string>"
  elif [[ "$id" == "video" ]]; then
    extra_environment="
      <key>VIDEO_PROXY_FFPROBE_COMMAND</key>
      <string>$(xml_escape "$FFPROBE_BIN")</string>"
  elif [[ "$id" == "vision" ]]; then
    extra_environment="
      <key>VISION_PROXY_FFMPEG_COMMAND</key>
      <string>$(xml_escape "$FFMPEG_BIN")</string>"
  elif [[ "$id" == "youtube" ]]; then
    extra_environment="
      <key>YOUTUBE_PROXY_YTDLP_BIN</key>
      <string>$(xml_escape "$YTDLP_BIN")</string>
      <key>YOUTUBE_PROXY_FFPROBE_BIN</key>
      <string>$(xml_escape "$FFPROBE_BIN")</string>
      <key>YOUTUBE_PROXY_FFMPEG_BIN</key>
      <string>$(xml_escape "$FFMPEG_BIN")</string>"
  elif [[ "$id" == "audio" ]]; then
    extra_environment="
      <key>AUDIO_PROXY_FFMPEG_BIN</key>
      <string>$(xml_escape "$FFMPEG_BIN")</string>
      <key>AUDIO_PROXY_FFPROBE_BIN</key>
      <string>$(xml_escape "$FFPROBE_BIN")</string>
      <key>AUDIO_PROXY_WHISPER_BIN</key>
      <string>$(xml_escape "$WHISPER_BIN")</string>
      <key>AUDIO_PROXY_WHISPER_MODEL</key>
      <string>$(xml_escape "$package/runtime/models/ggml-large-v3-turbo.bin")</string>
      <key>AUDIO_PROXY_WHISPER_MODEL_SHA256</key>
      <string>$(xml_escape "$WHISPER_MODEL_SHA256")</string>"
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
    <string>$(xml_escape "$entry")</string>
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
    <key>PD20_MODEL</key>
    <string>PD20</string>
    <key>PD20_LINK_MISSING_THRESHOLD</key>
    <string>2</string>
    <key>PD20_ADB_RESTART_COOLDOWN_SECONDS</key>
    <string>300</string>
    <key>PD20_DEVICE_READY_TIMEOUT_SECONDS</key>
    <string>5</string>
    <key>PD20_WIRELESS_ADB_ENABLED</key>
    <string>$(xml_escape "$PD20_WIRELESS_ADB_ENABLED")</string>
    <key>IOREG_BIN</key>
    <string>/usr/sbin/ioreg</string>
  </dict>
  <key>StartInterval</key>
  <integer>10</integer>
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

for id in grok video codex image vision brush draw conversation audio youtube manager; do
  write_proxy_plist "$id"
done
write_watchdog_plist

if [[ "$RENDER_ONLY" == "true" ]]; then
  printf 'launchd plists rendered and validated in %s\n' "$DESTINATION"
  exit 0
fi

# Existing watchdog state survives package synchronization. Reset only the
# host-readiness grace window before bootstrapping the freshly replaced proxy
# processes, otherwise a previous started-at value can trigger a restart loop
# while dependencies are still coming online.
umask 077
date +%s >"$MIRROR_ROOT/runtime/watchdog/started-at"
printf '0\n' >"$MIRROR_ROOT/runtime/watchdog/consecutive-failures"
chmod 600 \
  "$MIRROR_ROOT/runtime/watchdog/started-at" \
  "$MIRROR_ROOT/runtime/watchdog/consecutive-failures"

for label in \
  ai.coreline.heybot.proxy-codex \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-vision \
  ai.coreline.heybot.proxy-grok \
  ai.coreline.heybot.proxy-video \
  ai.coreline.heybot.proxy-brush \
  ai.coreline.heybot.proxy-audio \
  ai.coreline.heybot.proxy-youtube \
  ai.coreline.heybot.proxy-draw \
  ai.coreline.heybot.proxy-conversation \
  ai.coreline.heybot.proxy-manager \
  ai.coreline.heybot.proxy-watchdog; do
  launchctl bootstrap "$DOMAIN" "$DESTINATION/$label.plist"
done

for label in \
  ai.coreline.heybot.proxy-grok \
  ai.coreline.heybot.proxy-video \
  ai.coreline.heybot.proxy-codex \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-vision \
  ai.coreline.heybot.proxy-brush \
  ai.coreline.heybot.proxy-audio \
  ai.coreline.heybot.proxy-youtube \
  ai.coreline.heybot.proxy-draw \
  ai.coreline.heybot.proxy-conversation \
  ai.coreline.heybot.proxy-manager; do
  launchctl kickstart -k "$DOMAIN/$label"
done

printf 'Independent proxy launchd services installed from mirror: %s\n' "$MIRROR_ROOT"
