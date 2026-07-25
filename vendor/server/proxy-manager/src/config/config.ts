import { resolve } from "node:path";

export interface ManagerConfig {
  host: string;
  port: number;
  routeSecretFile: string;
  adminSecretFile: string;
  registryFile: string;
  requestMaxBytes: number;
  connectTimeoutMs: number;
  controlTimeoutMs: number;
  streamIdleTimeoutMs: number;
  healthTimeoutMs: number;
  lifecycleEnabled: boolean;
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

export function loadManagerConfig(
  env: NodeJS.ProcessEnv = process.env,
  cwd = process.cwd(),
): ManagerConfig {
  const host = env.MANAGER_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) {
    throw new Error("MANAGER_HOST must be loopback");
  }
  const lifecycleRaw = (env.MANAGER_LIFECYCLE_ENABLED || "false").toLowerCase();
  if (!["true", "false"].includes(lifecycleRaw)) {
    throw new Error("MANAGER_LIFECYCLE_ENABLED must be true or false");
  }
  return {
    host,
    port: integer(env, "MANAGER_PORT", 4340, 1, 65535),
    routeSecretFile: path(
      env,
      "MANAGER_ROUTE_SECRET_FILE",
      "./runtime/secrets/route.secret",
      cwd,
    ),
    adminSecretFile: path(
      env,
      "MANAGER_ADMIN_SECRET_FILE",
      "./runtime/secrets/admin.secret",
      cwd,
    ),
    registryFile: path(
      env,
      "MANAGER_PROXY_REGISTRY_FILE",
      "./config/proxies.json",
      cwd,
    ),
    requestMaxBytes: integer(env, "MANAGER_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    connectTimeoutMs: integer(env, "MANAGER_CONNECT_TIMEOUT_MS", 3_000, 100, 60_000),
    controlTimeoutMs: integer(
      env,
      "MANAGER_UPSTREAM_CONTROL_TIMEOUT_MS",
      15_000,
      100,
      120_000,
    ),
    streamIdleTimeoutMs: integer(
      env,
      "MANAGER_STREAM_IDLE_TIMEOUT_MS",
      120_000,
      1_000,
      3_600_000,
    ),
    healthTimeoutMs: integer(env, "MANAGER_HEALTH_TIMEOUT_MS", 3_000, 100, 60_000),
    lifecycleEnabled: lifecycleRaw === "true",
  };
}
