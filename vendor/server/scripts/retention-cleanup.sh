#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MODE="${1:---dry-run}"
[[ "$MODE" == "--dry-run" || "$MODE" == "--execute" ]] ||
  { printf '%s\n' "usage: retention-cleanup.sh [--dry-run|--execute]" >&2; exit 2; }

cleanup() {
  local directory="$1"
  local minutes="$2"
  local label="$3"
  [[ -d "$directory" ]] || return 0
  while IFS= read -r -d '' path; do
    printf '%s %s\n' "$label" "$path"
    [[ "$MODE" == "--execute" ]] && rm -rf -- "$path"
  done < <(find "$directory" -mindepth 1 -maxdepth 1 -type d -mmin "+$minutes" -print0)
}

cleanup "$ROOT/proxy-codex/runtime/artifacts" "${CODEX_RETENTION_MINUTES:-60}" "codex-artifact"
cleanup "$ROOT/proxy-codex/runtime/jobs" "${CODEX_RETENTION_MINUTES:-60}" "codex-workspace"
cleanup "$ROOT/proxy-codex/runtime/vision" "${CODEX_RETENTION_MINUTES:-60}" "codex-vision-workspace"
cleanup "$ROOT/proxy-image/runtime/artifacts" "${IMAGE_RETENTION_MINUTES:-1440}" "image-artifact"
