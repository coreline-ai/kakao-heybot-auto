#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
: "${GROK_PROXY_CLI_COMMAND:?set an absolute Grok CLI command}"
: "${GROK_PROXY_CLI_HOME:?set the dedicated CLI home}"
: "${GROK_PROXY_SESSION_ROOT:?set the CLI session root}"
[[ "$GROK_PROXY_CLI_COMMAND" = /* && -x "$GROK_PROXY_CLI_COMMAND" ]]
[[ "$GROK_PROXY_CLI_HOME" = /* && -d "$GROK_PROXY_CLI_HOME" ]]
[[ "$GROK_PROXY_SESSION_ROOT" = /* && -d "$GROK_PROXY_SESSION_ROOT" ]]
[[ -s "$ROOT/runtime/secrets/video-upstream.secret" ]]
"$GROK_PROXY_CLI_COMMAND" --version >/dev/null
printf '%s\n' 'proxy-grok configuration ready'
