#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ -f "$ROOT/dist/src/index.js" ]] || { printf '%s\n' 'conversation proxy is not built' >&2; exit 1; }
[[ -s "$ROOT/runtime/secrets/manager.secret" ]] || { printf '%s\n' 'conversation manager secret is missing' >&2; exit 1; }
[[ -s "$ROOT/runtime/secrets/codex-conversation.secret" ]] || { printf '%s\n' 'codex conversation secret is missing' >&2; exit 1; }
[[ -s "$ROOT/runtime/secrets/grok-conversation.secret" ]] || { printf '%s\n' 'grok conversation secret is missing' >&2; exit 1; }
printf '%s\n' 'proxy-conversation ready'
