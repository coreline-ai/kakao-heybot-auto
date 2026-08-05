#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for package in proxy-manager proxy-conversation proxy-audio proxy-vision proxy-image proxy-codex; do
  "$ROOT/$package/scripts/stop.sh"
done
