import { isAbsolute, resolve } from "node:path";

export interface VideoProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  grokBaseUrl: string;
  grokServiceId: string;
  grokSecretFile: string;
  grokJobTimeoutMs: number;
  queueConcurrency: number;
  queueMaxPending: number;
  queueMaxPendingPerRoom: number;
  queueWaitTimeoutMs: number;
  promptMaxChars: number;
  requestMaxBytes: number;
  videoMaxBytes: number;
  artifactRetentionHours: number;
  ffprobeCommand: string;
  publicPublishEnabled: false;
  runtimeDir: string;
  databaseFile: string;
}

function integer(
  env: NodeJS.ProcessEnv,
  name: string,
  fallback: number,
  minimum: number,
  maximum: number,
): number {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const parsed = Number(raw);
  if (!Number.isSafeInteger(parsed) || parsed < minimum || parsed > maximum) {
    throw new Error(`${name} must be between ${minimum} and ${maximum}`);
  }
  return parsed;
}

function path(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  return resolve(cwd, env[name]?.trim() || fallback);
}

export function loadVideoProxyConfig(
  env: NodeJS.ProcessEnv = process.env,
  cwd = process.cwd(),
): VideoProxyConfig {
  const host = env.VIDEO_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
    throw new Error("VIDEO_PROXY_HOST must be loopback");
  }
  const grokBaseUrl = env.VIDEO_PROXY_GROK_BASE_URL?.trim() || "http://127.0.0.1:4358";
  const grokUrl = new URL(grokBaseUrl);
  if (
    grokUrl.protocol !== "http:" ||
    !["127.0.0.1", "::1", "localhost"].includes(grokUrl.hostname)
  ) {
    throw new Error("VIDEO_PROXY_GROK_BASE_URL must be loopback HTTP");
  }
  const runtimeDir = path(env, "VIDEO_PROXY_RUNTIME_DIR", "./runtime", cwd);
  const ffprobeCommand = env.VIDEO_PROXY_FFPROBE_COMMAND?.trim();
  if (!ffprobeCommand || !isAbsolute(ffprobeCommand)) {
    throw new Error("VIDEO_PROXY_FFPROBE_COMMAND must be an absolute path");
  }
  if ((env.VIDEO_PROXY_PUBLIC_PUBLISH_ENABLED ?? "false").toLowerCase() !== "false") {
    throw new Error("Public publishing is disabled in this release");
  }
  return {
    host,
    port: integer(env, "VIDEO_PROXY_PORT", 4357, 1, 65535),
    managerSecretFile: path(
      env,
      "VIDEO_PROXY_MANAGER_SECRET_FILE",
      "./runtime/secrets/manager.secret",
      cwd,
    ),
    grokBaseUrl: grokUrl.origin,
    grokServiceId: env.VIDEO_PROXY_GROK_SERVICE_ID?.trim() || "video",
    grokSecretFile: path(
      env,
      "VIDEO_PROXY_GROK_SECRET_FILE",
      "./runtime/secrets/grok-upstream.secret",
      cwd,
    ),
    grokJobTimeoutMs: integer(
      env,
      "VIDEO_PROXY_GROK_JOB_TIMEOUT_MS",
      1_800_000,
      10_000,
      3_600_000,
    ),
    queueConcurrency: integer(env, "VIDEO_PROXY_QUEUE_CONCURRENCY", 1, 1, 4),
    queueMaxPending: integer(env, "VIDEO_PROXY_QUEUE_MAX_PENDING", 2, 1, 200),
    queueMaxPendingPerRoom: integer(
      env,
      "VIDEO_PROXY_QUEUE_MAX_PENDING_PER_ROOM",
      1,
      1,
      20,
    ),
    queueWaitTimeoutMs: integer(
      env,
      "VIDEO_PROXY_QUEUE_WAIT_TIMEOUT_MS",
      3_600_000,
      1_000,
      86_400_000,
    ),
    promptMaxChars: integer(env, "VIDEO_PROXY_PROMPT_MAX_CHARS", 1_000, 1, 4_000),
    requestMaxBytes: integer(env, "VIDEO_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    videoMaxBytes: integer(
      env,
      "VIDEO_PROXY_VIDEO_MAX_BYTES",
      52_428_800,
      1_024,
      104_857_600,
    ),
    ffprobeCommand,
    artifactRetentionHours: integer(
      env,
      "VIDEO_PROXY_ARTIFACT_RETENTION_HOURS",
      24,
      1,
      720,
    ),
    publicPublishEnabled: false,
    runtimeDir,
    databaseFile: resolve(runtimeDir, "db", "jobs.sqlite3"),
  };
}
