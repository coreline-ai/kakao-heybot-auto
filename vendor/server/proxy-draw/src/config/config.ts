import { resolve } from "node:path";

export interface DrawProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  codexBaseUrl: string;
  codexServiceId: string;
  codexSecretFile: string;
  brushBaseUrl: string;
  brushServiceId: string;
  brushSecretFile: string;
  codexJobTimeoutMs: number;
  brushJobTimeoutMs: number;
  queueConcurrency: number;
  queueMaxPending: number;
  queueMaxPendingPerRoom: number;
  queueWaitTimeoutMs: number;
  promptMaxChars: number;
  requestMaxBytes: number;
  imageMaxBytes: number;
  videoMaxBytes: number;
  artifactRetentionHours: number;
  runtimeDir: string;
  databaseFile: string;
  ffprobeCommand: string;
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, minimum: number, maximum: number): number {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error(`${name} out of range`);
  return value;
}

function path(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  return resolve(cwd, env[name]?.trim() || fallback);
}

function loopback(env: NodeJS.ProcessEnv, name: string, fallback: string): string {
  const value = env[name]?.trim() || fallback;
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "::1", "localhost"].includes(url.hostname)) {
    throw new Error(`${name} must be loopback HTTP`);
  }
  return url.origin;
}

export function loadDrawProxyConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): DrawProxyConfig {
  const host = env.DRAW_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) throw new Error("DRAW_PROXY_HOST must be loopback");
  const runtimeDir = path(env, "DRAW_PROXY_RUNTIME_DIR", "./runtime", cwd);
  return {
    host,
    port: integer(env, "DRAW_PROXY_PORT", 4359, 1, 65535),
    managerSecretFile: path(env, "DRAW_PROXY_MANAGER_SECRET_FILE", "./runtime/secrets/manager.secret", cwd),
    codexBaseUrl: loopback(env, "DRAW_PROXY_CODEX_BASE_URL", "http://127.0.0.1:4348"),
    codexServiceId: env.DRAW_PROXY_CODEX_SERVICE_ID?.trim() || "draw",
    codexSecretFile: path(env, "DRAW_PROXY_CODEX_SECRET_FILE", "./runtime/secrets/codex-upstream.secret", cwd),
    brushBaseUrl: loopback(env, "DRAW_PROXY_BRUSH_BASE_URL", "http://127.0.0.1:4360"),
    brushServiceId: env.DRAW_PROXY_BRUSH_SERVICE_ID?.trim() || "draw",
    brushSecretFile: path(env, "DRAW_PROXY_BRUSH_SECRET_FILE", "./runtime/secrets/brush-upstream.secret", cwd),
    codexJobTimeoutMs: integer(env, "DRAW_PROXY_CODEX_JOB_TIMEOUT_MS", 600_000, 5_000, 3_600_000),
    brushJobTimeoutMs: integer(env, "DRAW_PROXY_BRUSH_JOB_TIMEOUT_MS", 900_000, 60_000, 3_600_000),
    queueConcurrency: integer(env, "DRAW_PROXY_QUEUE_CONCURRENCY", 1, 1, 1),
    queueMaxPending: integer(env, "DRAW_PROXY_QUEUE_MAX_PENDING", 1, 1, 20),
    queueMaxPendingPerRoom: integer(env, "DRAW_PROXY_QUEUE_MAX_PENDING_PER_ROOM", 1, 1, 5),
    queueWaitTimeoutMs: integer(env, "DRAW_PROXY_QUEUE_WAIT_TIMEOUT_MS", 3_600_000, 1_000, 86_400_000),
    promptMaxChars: integer(env, "DRAW_PROXY_PROMPT_MAX_CHARS", 300, 1, 1_000),
    requestMaxBytes: integer(env, "DRAW_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    imageMaxBytes: integer(env, "DRAW_PROXY_IMAGE_MAX_BYTES", 12_582_912, 1_024, 50_331_648),
    videoMaxBytes: integer(env, "DRAW_PROXY_VIDEO_MAX_BYTES", 33_554_432, 1_024, 200 * 1024 * 1024),
    artifactRetentionHours: integer(env, "DRAW_PROXY_ARTIFACT_RETENTION_HOURS", 24, 1, 720),
    runtimeDir,
    databaseFile: resolve(runtimeDir, "db", "jobs.sqlite3"),
    ffprobeCommand: env.DRAW_PROXY_FFPROBE_COMMAND?.trim() || "ffprobe",
  };
}
