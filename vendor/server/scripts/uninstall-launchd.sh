#!/usr/bin/env bash
set -euo pipefail

DOMAIN="gui/$(id -u)"
DESTINATION="${HOME}/Library/LaunchAgents"
for label in \
  ai.coreline.heybot.proxy-watchdog \
  ai.coreline.heybot.proxy-manager \
  ai.coreline.heybot.proxy-image \
  ai.coreline.heybot.proxy-codex; do
  launchctl bootout "$DOMAIN/$label" 2>/dev/null || true
  rm -f "$DESTINATION/$label.plist"
done
printf '%s\n' "HeyBot proxy launchd services removed."
