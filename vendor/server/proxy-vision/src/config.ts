import { isAbsolute, resolve } from "node:path";

export interface VisionConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  codexBaseUrl: string;
  codexServiceId: string;
  codexSecretFile: string;
  allowedSourceHost: string;
  requestMaxBytes: number;
  imageMaxBytes: number;
  ffmpegCommand: string;
  fetchTimeoutMs: number;
  codexTimeoutMs: number;
  queueConcurrency: number;
  queueMaxPending: number;
  queueMaxPendingPerRoom: number;
  queueWaitTimeoutMs: number;
  runtimeDir: string;
  databaseFile: string;
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed < min || parsed > max) throw new Error(`${name} out of range`);
  return parsed;
}

function path(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  return resolve(cwd, env[name]?.trim() || fallback);
}

export function loadVisionConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): VisionConfig {
  const host = env.VISION_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) throw new Error("VISION_PROXY_HOST must be loopback");
  const codex = new URL(env.VISION_PROXY_CODEX_BASE_URL?.trim() || "http://127.0.0.1:4348");
  if (codex.protocol !== "http:" || !["127.0.0.1", "::1", "localhost"].includes(codex.hostname)) {
    throw new Error("VISION_PROXY_CODEX_BASE_URL must be loopback HTTP");
  }
  const sourceHost = env.VISION_PROXY_ALLOWED_SOURCE_HOST?.trim() || "talk.kakaocdn.net";
  if (sourceHost !== "talk.kakaocdn.net") throw new Error("Unsupported source allowlist");
  const ffmpegCommand = env.VISION_PROXY_FFMPEG_COMMAND?.trim() || "/opt/homebrew/bin/ffmpeg";
  if (!isAbsolute(ffmpegCommand)) throw new Error("VISION_PROXY_FFMPEG_COMMAND must be absolute");
  const runtimeDir = path(env, "VISION_PROXY_RUNTIME_DIR", "./runtime", cwd);
  return {
    host,
    port: integer(env, "VISION_PROXY_PORT", 4362, 1, 65535),
    managerSecretFile: path(env, "VISION_PROXY_MANAGER_SECRET_FILE", "./runtime/secrets/manager.secret", cwd),
    codexBaseUrl: codex.origin,
    codexServiceId: env.VISION_PROXY_CODEX_SERVICE_ID?.trim() || "vision",
    codexSecretFile: path(env, "VISION_PROXY_CODEX_SECRET_FILE", "./runtime/secrets/codex-upstream.secret", cwd),
    allowedSourceHost: sourceHost,
    requestMaxBytes: integer(env, "VISION_PROXY_REQUEST_MAX_BYTES", 16_384, 1_024, 65_536),
    imageMaxBytes: integer(env, "VISION_PROXY_IMAGE_MAX_BYTES", 10 * 1024 * 1024, 1_024, 20 * 1024 * 1024),
    ffmpegCommand,
    fetchTimeoutMs: integer(env, "VISION_PROXY_FETCH_TIMEOUT_MS", 20_000, 1_000, 120_000),
    codexTimeoutMs: integer(env, "VISION_PROXY_CODEX_TIMEOUT_MS", 100_000, 5_000, 300_000),
    queueConcurrency: integer(env, "VISION_PROXY_QUEUE_CONCURRENCY", 1, 1, 4),
    queueMaxPending: integer(env, "VISION_PROXY_QUEUE_MAX_PENDING", 12, 1, 100),
    queueMaxPendingPerRoom: integer(env, "VISION_PROXY_QUEUE_MAX_PENDING_PER_ROOM", 2, 1, 10),
    queueWaitTimeoutMs: integer(env, "VISION_PROXY_QUEUE_WAIT_TIMEOUT_MS", 120_000, 1_000, 600_000),
    runtimeDir,
    databaseFile: resolve(runtimeDir, "db", "jobs.sqlite3"),
  };
}
