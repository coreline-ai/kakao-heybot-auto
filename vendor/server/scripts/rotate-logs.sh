#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MAX_BYTES="${PROXY_LOG_MAX_BYTES:-10485760}"
KEEP="${PROXY_LOG_KEEP_FILES:-5}"

rotate() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  local bytes
  bytes="$(stat -f '%z' "$file")"
  (( bytes >= MAX_BYTES )) || return 0
  rm -f "$file.$KEEP"
  local index
  for ((index = KEEP - 1; index >= 1; index--)); do
    [[ -f "$file.$index" ]] && mv "$file.$index" "$file.$((index + 1))"
  done
  mv "$file" "$file.1"
  : >"$file"
  chmod 600 "$file"
}

for package in proxy-codex proxy-image proxy-vision proxy-manager; do
  for file in "$ROOT/$package/runtime/logs/"*.log; do
    [[ -e "$file" ]] && rotate "$file"
  done
done
