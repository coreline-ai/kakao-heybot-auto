import { resolve } from "node:path";

export interface CodexProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  callerSecretsDir: string;
  capabilitiesFile: string;
  cliBin: string;
  codexHome?: string;
  imageModel?: string;
  queueConcurrency: number;
  queueMaxPending: number;
  queueWaitTimeoutMs: number;
  jobTimeoutMs: number;
  requestMaxBytes: number;
  artifactMaxBytes: number;
  artifactRetentionHours: number;
  runtimeDir: string;
  databaseFile: string;
  runnerMode: "codex" | "fake";
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

function filePath(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  const value = env[name]?.trim() || fallback;
  return resolve(cwd, value);
}

export function loadCodexProxyConfig(
  env: NodeJS.ProcessEnv = process.env,
  cwd = process.cwd(),
): CodexProxyConfig {
  const host = env.CODEX_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
    throw new Error("CODEX_PROXY_HOST must be loopback");
  }
  const runtimeDir = filePath(env, "CODEX_PROXY_RUNTIME_DIR", "./runtime", cwd);
  const runnerMode = env.CODEX_PROXY_RUNNER?.trim() || "codex";
  if (runnerMode !== "codex" && runnerMode !== "fake") {
    throw new Error("CODEX_PROXY_RUNNER must be codex or fake");
  }

  return {
    host,
    port: integer(env, "CODEX_PROXY_PORT", 4348, 1, 65535),
    managerSecretFile: filePath(
      env,
      "CODEX_PROXY_MANAGER_SECRET_FILE",
      "./runtime/secrets/manager.secret",
      cwd,
    ),
    callerSecretsDir: filePath(
      env,
      "CODEX_PROXY_CALLER_SECRETS_DIR",
      "./runtime/secrets/callers",
      cwd,
    ),
    capabilitiesFile: filePath(
      env,
      "CODEX_PROXY_CAPABILITIES_FILE",
      "./config/capabilities.json",
      cwd,
    ),
    cliBin: env.CODEX_CLI_BIN?.trim() || "codex",
    codexHome: env.CODEX_HOME?.trim() || undefined,
    imageModel: env.CODEX_IMAGE_MODEL?.trim() || undefined,
    queueConcurrency: integer(env, "CODEX_PROXY_QUEUE_CONCURRENCY", 1, 1, 8),
    queueMaxPending: integer(env, "CODEX_PROXY_QUEUE_MAX_PENDING", 8, 1, 100),
    queueWaitTimeoutMs: integer(
      env,
      "CODEX_PROXY_QUEUE_WAIT_TIMEOUT_MS",
      600_000,
      1_000,
      86_400_000,
    ),
    jobTimeoutMs: integer(env, "CODEX_PROXY_JOB_TIMEOUT_MS", 360_000, 5_000, 3_600_000),
    requestMaxBytes: integer(env, "CODEX_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    artifactMaxBytes: integer(
      env,
      "CODEX_PROXY_ARTIFACT_MAX_BYTES",
      12_582_912,
      1_024,
      50_331_648,
    ),
    artifactRetentionHours: integer(
      env,
      "CODEX_PROXY_ARTIFACT_RETENTION_HOURS",
      1,
      1,
      168,
    ),
    runtimeDir,
    databaseFile: resolve(runtimeDir, "db", "jobs.sqlite3"),
    runnerMode,
  };
}
