import { resolve } from "node:path";

export interface AudioProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  transcriptKeyFile: string;
  runtimeDir: string;
  databaseFile: string;
  runnerMode: "cli" | "fake";
  ffmpegBin: string;
  ffprobeBin: string;
  whisperBin: string;
  whisperModel: string;
  whisperModelSha256?: string;
  requestMaxBytes: number;
  sourceMaxBytes: number;
  sourceTimeoutMs: number;
  processTimeoutMs: number;
  maxDurationMs: number;
  maxConcurrency: number;
  maxPendingPerRoom: number;
  transcriptTtlMs: number;
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} is out of range`);
  return value;
}

function path(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  return resolve(cwd, env[name]?.trim() || fallback);
}

export function loadAudioProxyConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): AudioProxyConfig {
  const host = env.AUDIO_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error("AUDIO_PROXY_HOST must be loopback");
  const runner = (env.AUDIO_PROXY_RUNNER_MODE || "cli").trim().toLowerCase();
  if (!['cli', 'fake'].includes(runner)) throw new Error("AUDIO_PROXY_RUNNER_MODE must be cli or fake");
  const runtimeDir = path(env, "AUDIO_PROXY_RUNTIME_DIR", "./runtime", cwd);
  return {
    host,
    port: integer(env, "AUDIO_PROXY_PORT", 4363, 1, 65535),
    managerSecretFile: path(env, "AUDIO_PROXY_MANAGER_SECRET_FILE", "./runtime/secrets/manager.secret", cwd),
    transcriptKeyFile: path(env, "AUDIO_PROXY_TRANSCRIPT_KEY_FILE", "./runtime/secrets/transcript.key", cwd),
    runtimeDir,
    databaseFile: path(env, "AUDIO_PROXY_DATABASE_FILE", "./runtime/db/audio.sqlite3", cwd),
    runnerMode: runner as "cli" | "fake",
    ffmpegBin: env.AUDIO_PROXY_FFMPEG_BIN?.trim() || "ffmpeg",
    ffprobeBin: env.AUDIO_PROXY_FFPROBE_BIN?.trim() || "ffprobe",
    whisperBin: env.AUDIO_PROXY_WHISPER_BIN?.trim() || "whisper-cli",
    whisperModel: path(env, "AUDIO_PROXY_WHISPER_MODEL", "./runtime/models/ggml-large-v3-turbo.bin", cwd),
    whisperModelSha256: env.AUDIO_PROXY_WHISPER_MODEL_SHA256?.trim().toLowerCase() || undefined,
    requestMaxBytes: integer(env, "AUDIO_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    sourceMaxBytes: integer(env, "AUDIO_PROXY_SOURCE_MAX_BYTES", 100 * 1024 * 1024, 1_024, 200 * 1024 * 1024),
    sourceTimeoutMs: integer(env, "AUDIO_PROXY_SOURCE_TIMEOUT_MS", 60_000, 1_000, 300_000),
    processTimeoutMs: integer(env, "AUDIO_PROXY_PROCESS_TIMEOUT_MS", 30 * 60_000, 10_000, 3_600_000),
    maxDurationMs: integer(env, "AUDIO_PROXY_MAX_DURATION_MS", 120 * 60_000, 1_000, 7_200_000),
    maxConcurrency: integer(env, "AUDIO_PROXY_MAX_CONCURRENCY", 2, 1, 8),
    maxPendingPerRoom: integer(env, "AUDIO_PROXY_MAX_PENDING_PER_ROOM", 1, 1, 8),
    transcriptTtlMs: integer(env, "AUDIO_PROXY_TRANSCRIPT_TTL_MS", 24 * 60 * 60_000, 60_000, 7 * 24 * 60 * 60_000),
  };
}
