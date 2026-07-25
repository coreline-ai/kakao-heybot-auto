import { resolve } from "node:path";

export interface ImageProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  codexBaseUrl: string;
  codexServiceId: string;
  codexSecretFile: string;
  codexJobTimeoutMs: number;
  queueConcurrency: number;
  queueMaxPending: number;
  queueMaxPendingPerRoom: number;
  queueWaitTimeoutMs: number;
  promptMaxChars: number;
  requestMaxBytes: number;
  imageMaxBytes: number;
  artifactRetentionHours: number;
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

export function loadImageProxyConfig(
  env: NodeJS.ProcessEnv = process.env,
  cwd = process.cwd(),
): ImageProxyConfig {
  const host = env.IMAGE_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
    throw new Error("IMAGE_PROXY_HOST must be loopback");
  }
  const codexBaseUrl = env.IMAGE_PROXY_CODEX_BASE_URL?.trim() || "http://127.0.0.1:4348";
  const codexUrl = new URL(codexBaseUrl);
  if (
    codexUrl.protocol !== "http:" ||
    !["127.0.0.1", "::1", "localhost"].includes(codexUrl.hostname)
  ) {
    throw new Error("IMAGE_PROXY_CODEX_BASE_URL must be loopback HTTP");
  }
  const runtimeDir = path(env, "IMAGE_PROXY_RUNTIME_DIR", "./runtime", cwd);
  if ((env.IMAGE_PROXY_PUBLIC_PUBLISH_ENABLED ?? "false").toLowerCase() !== "false") {
    throw new Error("Public publishing is disabled in this release");
  }
  return {
    host,
    port: integer(env, "IMAGE_PROXY_PORT", 4347, 1, 65535),
    managerSecretFile: path(
      env,
      "IMAGE_PROXY_MANAGER_SECRET_FILE",
      "./runtime/secrets/manager.secret",
      cwd,
    ),
    codexBaseUrl: codexUrl.origin,
    codexServiceId: env.IMAGE_PROXY_CODEX_SERVICE_ID?.trim() || "image",
    codexSecretFile: path(
      env,
      "IMAGE_PROXY_CODEX_SECRET_FILE",
      "./runtime/secrets/codex-upstream.secret",
      cwd,
    ),
    codexJobTimeoutMs: integer(
      env,
      "IMAGE_PROXY_CODEX_JOB_TIMEOUT_MS",
      600_000,
      5_000,
      3_600_000,
    ),
    queueConcurrency: integer(env, "IMAGE_PROXY_QUEUE_CONCURRENCY", 1, 1, 4),
    queueMaxPending: integer(env, "IMAGE_PROXY_QUEUE_MAX_PENDING", 20, 1, 200),
    queueMaxPendingPerRoom: integer(
      env,
      "IMAGE_PROXY_QUEUE_MAX_PENDING_PER_ROOM",
      3,
      1,
      20,
    ),
    queueWaitTimeoutMs: integer(
      env,
      "IMAGE_PROXY_QUEUE_WAIT_TIMEOUT_MS",
      3_600_000,
      1_000,
      86_400_000,
    ),
    promptMaxChars: integer(env, "IMAGE_PROXY_PROMPT_MAX_CHARS", 1_000, 1, 4_000),
    requestMaxBytes: integer(env, "IMAGE_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    imageMaxBytes: integer(
      env,
      "IMAGE_PROXY_IMAGE_MAX_BYTES",
      12_582_912,
      1_024,
      50_331_648,
    ),
    artifactRetentionHours: integer(
      env,
      "IMAGE_PROXY_ARTIFACT_RETENTION_HOURS",
      24,
      1,
      720,
    ),
    publicPublishEnabled: false,
    runtimeDir,
    databaseFile: resolve(runtimeDir, "db", "jobs.sqlite3"),
  };
}
