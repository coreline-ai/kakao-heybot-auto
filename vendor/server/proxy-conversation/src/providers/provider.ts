import { readSecret } from "../auth/auth.js";
import type { ConversationProxyConfig } from "../config/config.js";

export type Engine = "codex" | "grok";
export interface Message { role: "system" | "user" | "assistant"; content: string; }
export interface ConversationInput { requestId: string; messages: Message[]; }
export interface ProviderResponse { requestId: string; engine: Engine; text: string; latencyMillis?: number; }

async function callProvider(
  baseUrl: string,
  path: string,
  secretFile: string,
  input: ConversationInput,
  serviceId: string,
  timeoutMs: number,
): Promise<ProviderResponse> {
  const response = await fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${readSecret(secretFile)}`,
      "content-type": "application/json",
      "x-heybot-service-id": "conversation",
    },
    body: JSON.stringify({ requestId: input.requestId, capability: "conversation.respond.v1", input: { messages: input.messages } }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const body = await response.json().catch(() => ({})) as Record<string, unknown>;
  if (!response.ok) throw new Error(typeof (body.error as Record<string, unknown> | undefined)?.code === "string" ? (body.error as Record<string, string>).code : `${serviceId.toUpperCase()}_TEXT_FAILED`);
  if (typeof body.text !== "string" || !body.text.trim()) throw new Error(`${serviceId.toUpperCase()}_TEXT_OUTPUT_INVALID`);
  return { requestId: String(body.requestId || input.requestId), engine: serviceId as Engine, text: body.text.trim(), latencyMillis: typeof body.latencyMillis === "number" ? body.latencyMillis : undefined };
}

export function providerFor(engine: Engine, config: ConversationProxyConfig): (input: ConversationInput) => Promise<ProviderResponse> {
  return engine === "codex"
    ? (input) => callProvider(config.codexBaseUrl, "/internal/v1/codex/conversation", config.codexSecretFile, input, "codex", config.timeoutMs)
    : (input) => callProvider(config.grokBaseUrl, "/internal/v1/grok/conversation", config.grokSecretFile, input, "grok", config.timeoutMs);
}
