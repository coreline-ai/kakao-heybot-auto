import { resolve, isAbsolute } from "node:path";

export interface GrokProxyConfig {
  host: string;
  port: number;
  videoServiceId: string;
  videoSecretFile: string;
  conversationServiceId: string;
  conversationSecretFile: string;
  cliCommand: string;
  cliHome: string;
  sessionRoot: string;
  runtimeDir: string;
  databaseFile: string;
  promptMaxChars: number;
  requestMaxBytes: number;
  artifactMaxBytes: number;
  jobTimeoutMs: number;
  queueMaxPending: number;
  artifactRetentionHours: number;
  textQueueConcurrency: number;
  textQueueMaxPending: number;
  textTimeoutMs: number;
  textMaxOutputChars: number;
}

function integer(env: NodeJS.ProcessEnv, name: string, fallback: number, min: number, max: number): number {
  const raw = env[name];
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < min || value > max) throw new Error(`${name} is out of range`);
  return value;
}
function abs(env: NodeJS.ProcessEnv, name: string, fallback: string, cwd: string): string {
  const value = env[name]?.trim() || fallback;
  const result = isAbsolute(value) ? value : resolve(cwd, value);
  if (!isAbsolute(result)) throw new Error(`${name} must be absolute`);
  return result;
}

export function loadGrokProxyConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): GrokProxyConfig {
  const host = env.GROK_PROXY_HOST?.trim() || "127.0.0.1";
  if (!['127.0.0.1', '::1', 'localhost'].includes(host)) throw new Error('GROK_PROXY_HOST must be loopback');
  const cliCommand = env.GROK_PROXY_CLI_COMMAND?.trim();
  if (!cliCommand || !isAbsolute(cliCommand)) throw new Error('GROK_PROXY_CLI_COMMAND must be an absolute path');
  return {
    host,
    port: integer(env, 'GROK_PROXY_PORT', 4358, 1, 65535),
    videoServiceId: env.GROK_PROXY_VIDEO_SERVICE_ID?.trim() || 'video',
    videoSecretFile: abs(env, 'GROK_PROXY_VIDEO_SECRET_FILE', './runtime/secrets/video-upstream.secret', cwd),
    conversationServiceId: env.GROK_PROXY_CONVERSATION_SERVICE_ID?.trim() || 'conversation',
    conversationSecretFile: abs(env, 'GROK_PROXY_CONVERSATION_SECRET_FILE', env.GROK_PROXY_VIDEO_SECRET_FILE?.trim() || './runtime/secrets/video-upstream.secret', cwd),
    cliCommand,
    cliHome: abs(env, 'GROK_PROXY_CLI_HOME', './runtime/grok-home', cwd),
    sessionRoot: abs(env, 'GROK_PROXY_SESSION_ROOT', './runtime/grok-sessions', cwd),
    runtimeDir: abs(env, 'GROK_PROXY_RUNTIME_DIR', './runtime', cwd),
    databaseFile: resolve(abs(env, 'GROK_PROXY_RUNTIME_DIR', './runtime', cwd), 'db', 'jobs.sqlite3'),
    promptMaxChars: integer(env, 'GROK_PROXY_PROMPT_MAX_CHARS', 1000, 1, 4000),
    requestMaxBytes: integer(env, 'GROK_PROXY_REQUEST_MAX_BYTES', 32768, 1024, 1048576),
    artifactMaxBytes: integer(env, 'GROK_PROXY_ARTIFACT_MAX_BYTES', 52428800, 1024, 104857600),
    jobTimeoutMs: integer(env, 'GROK_PROXY_JOB_TIMEOUT_MS', 1800000, 10000, 3600000),
    queueMaxPending: integer(env, 'GROK_PROXY_QUEUE_MAX_PENDING', 2, 1, 20),
    artifactRetentionHours: integer(env, 'GROK_PROXY_ARTIFACT_RETENTION_HOURS', 24, 1, 720),
    textQueueConcurrency: integer(env, 'GROK_TEXT_QUEUE_CONCURRENCY', 1, 1, 4),
    textQueueMaxPending: integer(env, 'GROK_TEXT_QUEUE_MAX_PENDING', 4, 1, 32),
    textTimeoutMs: integer(env, 'GROK_TEXT_TIMEOUT_MS', 90_000, 5_000, 300_000),
    textMaxOutputChars: integer(env, 'GROK_TEXT_MAX_OUTPUT_CHARS', 4_000, 64, 16_000),
  };
}
