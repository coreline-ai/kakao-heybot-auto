import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import { authenticate, readSecret } from "../auth/auth.js";
import type { ConversationProxyConfig } from "../config/config.js";
import { providerFor, type Engine, type Message } from "../providers/provider.js";

function json(response: ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": data.length, "cache-control": "no-store" });
  response.end(data);
}

async function readJson(request: IncomingMessage, max: number): Promise<unknown> {
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of request) { const value = Buffer.from(chunk); bytes += value.length; if (bytes > max) throw new Error("BODY_TOO_LARGE"); chunks.push(value); }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { throw new Error("INVALID_JSON"); }
}

function validate(value: unknown): { requestId: string; engine: Engine; messages: Message[] } {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_REQUEST");
  const body = value as Record<string, unknown>;
  if (Object.keys(body).some((key) => !["requestId", "engine", "kind", "promptVersion", "messages"].includes(key)) ||
    typeof body.requestId !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId) ||
    !["codex", "grok"].includes(String(body.engine)) || !Array.isArray(body.messages) || body.messages.length < 1 || body.messages.length > 32) throw new Error("INVALID_REQUEST");
  const messages = body.messages.map((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) throw new Error("INVALID_MESSAGE");
    const message = item as Record<string, unknown>;
    if (Object.keys(message).some((key) => !["role", "content"].includes(key)) || !["system", "user", "assistant"].includes(String(message.role)) || typeof message.content !== "string" || message.content.trim().length < 1 || message.content.length > 4_000) throw new Error("INVALID_MESSAGE");
    return { role: message.role as Message["role"], content: message.content.trim() };
  });
  return { requestId: body.requestId, engine: body.engine as Engine, messages };
}

export interface ConversationServerContext { server: Server; }

export function createConversationServer(config: ConversationProxyConfig): ConversationServerContext {
  const secret = readSecret(config.managerSecretFile);
  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") return json(response, 200, { ok: true, service: "proxy-conversation" });
      if (request.method === "GET" && url.pathname === "/ready") return json(response, 200, { ready: true, engines: ["codex", "grok"] });
      if (!authenticate(request.headers.authorization, secret)) return json(response, 401, { error: { code: "UNAUTHORIZED" } });
      if (request.method !== "POST" || url.pathname !== "/v1/conversation/respond") return json(response, 404, { error: { code: "NOT_FOUND" } });
      const input = validate(await readJson(request, config.requestMaxBytes));
      const result = await providerFor(input.engine, config)(input);
      return json(response, 200, result);
    } catch (error) {
      const code = (error instanceof Error ? error.message : "CONVERSATION_FAILED").slice(0, 64);
      return json(response, code === "BODY_TOO_LARGE" ? 413 : code.includes("UNAUTHORIZED") ? 401 : code.includes("TEXT_QUEUE_FULL") ? 429 : code.includes("TIMEOUT") ? 504 : 400, { error: { code } });
    }
  });
  return { server };
}
