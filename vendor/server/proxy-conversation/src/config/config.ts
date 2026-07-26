import { resolve } from "node:path";

export interface ConversationProxyConfig {
  host: string;
  port: number;
  managerSecretFile: string;
  codexBaseUrl: string;
  codexSecretFile: string;
  grokBaseUrl: string;
  grokSecretFile: string;
  requestMaxBytes: number;
  timeoutMs: number;
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

function loopbackUrl(value: string, name: string): string {
  const url = new URL(value);
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost", "::1"].includes(url.hostname)) throw new Error(`${name} must be loopback HTTP`);
  return value.replace(/\/$/, "");
}

export function loadConversationProxyConfig(env: NodeJS.ProcessEnv = process.env, cwd = process.cwd()): ConversationProxyConfig {
  const host = env.CONVERSATION_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "localhost", "::1"].includes(host)) throw new Error("CONVERSATION_PROXY_HOST must be loopback");
  return {
    host,
    port: integer(env, "CONVERSATION_PROXY_PORT", 4361, 1, 65535),
    managerSecretFile: path(env, "CONVERSATION_PROXY_MANAGER_SECRET_FILE", "./runtime/secrets/manager.secret", cwd),
    codexBaseUrl: loopbackUrl(env.CONVERSATION_CODEX_BASE_URL?.trim() || "http://127.0.0.1:4348", "CONVERSATION_CODEX_BASE_URL"),
    codexSecretFile: path(env, "CONVERSATION_CODEX_SECRET_FILE", "./runtime/secrets/codex-conversation.secret", cwd),
    grokBaseUrl: loopbackUrl(env.CONVERSATION_GROK_BASE_URL?.trim() || "http://127.0.0.1:4358", "CONVERSATION_GROK_BASE_URL"),
    grokSecretFile: path(env, "CONVERSATION_GROK_SECRET_FILE", "./runtime/secrets/grok-conversation.secret", cwd),
    requestMaxBytes: integer(env, "CONVERSATION_PROXY_REQUEST_MAX_BYTES", 32_768, 1_024, 1_048_576),
    timeoutMs: integer(env, "CONVERSATION_PROXY_TIMEOUT_MS", 100_000, 1_000, 300_000),
  };
}
