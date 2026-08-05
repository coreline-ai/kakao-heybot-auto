#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
npm run build >/dev/null
[[ -s "${AUDIO_PROXY_MANAGER_SECRET_FILE:-runtime/secrets/manager.secret}" ]]
key="${AUDIO_PROXY_TRANSCRIPT_KEY_FILE:-runtime/secrets/transcript.key}"
[[ -s "$key" && "$(tr -d '\r\n' <"$key" | wc -c | tr -d ' ')" == "64" ]]
command -v "${AUDIO_PROXY_FFMPEG_BIN:-ffmpeg}" >/dev/null
command -v "${AUDIO_PROXY_FFPROBE_BIN:-ffprobe}" >/dev/null
if [[ "${AUDIO_PROXY_RUNNER_MODE:-cli}" == "cli" ]]; then
  command -v "${AUDIO_PROXY_WHISPER_BIN:-whisper-cli}" >/dev/null
  [[ -s "${AUDIO_PROXY_WHISPER_MODEL:-runtime/models/ggml-large-v3-turbo.bin}" ]]
  [[ "${AUDIO_PROXY_WHISPER_MODEL_SHA256:-}" =~ ^[0-9a-f]{64}$ ]]
fi
printf 'proxy-audio doctor passed\n'
