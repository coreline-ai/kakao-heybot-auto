#!/usr/bin/env bash
set -euo pipefail

CODEX_BIN="${CODEX_CLI_BIN:-codex}"
command -v node >/dev/null
command -v "$CODEX_BIN" >/dev/null
node_version="$(node --version)"
codex_version="$("$CODEX_BIN" --version)"
login_status="$("$CODEX_BIN" login status 2>&1)"
[[ "$login_status" == Logged\ in* ]] || { printf '%s\n' "Codex auth is not ready." >&2; exit 1; }
"$CODEX_BIN" exec --help | grep -q -- "--enable <FEATURE>"

printf 'node=%s\ncodex=%s\nauth=ready\nimage_generation_flag=available\n' \
  "$node_version" "$codex_version"

if [[ "${CODEX_DOCTOR_RUN_CANARY:-false}" == "true" ]]; then
  work="${TMPDIR:-/tmp}/heybot-codex-doctor-canary"
  mkdir -p "$work"
  printf '%s\n' \
    "Use image generation to create one varied 1024x1024 PNG and save it to $work/canary.png. Reply only READY." |
    "$CODEX_BIN" exec --ephemeral --skip-git-repo-check --ignore-rules \
      --sandbox workspace-write --enable image_generation -C "$work" - >/dev/null
  sips -g pixelWidth -g pixelHeight -g format "$work/canary.png"
fi
