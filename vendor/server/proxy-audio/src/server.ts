import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import { authenticate, readSecret } from "./auth.js";
import type { AudioProxyConfig } from "./config.js";
import type { AudioProcessorPort } from "./processor.js";
import type { AudioCreateInput } from "./types.js";

function json(response: ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, { "content-type": "application/json; charset=utf-8", "content-length": data.length, "cache-control": "no-store" });
  response.end(data);
}

async function readJson(request: IncomingMessage, max: number): Promise<unknown> {
  const chunks: Buffer[] = []; let bytes = 0;
  for await (const chunk of request) { const value = Buffer.from(chunk); bytes += value.length; if (bytes > max) throw new Error("BODY_TOO_LARGE"); chunks.push(value); }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); } catch { throw new Error("INVALID_JSON"); }
}

function validate(value: unknown, sourceMaxBytes: number): AudioCreateInput {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_REQUEST");
  const body = value as Record<string, unknown>;
  if (Object.keys(body).some((key) => !["requestId", "chatId", "source", "language"].includes(key)) ||
    typeof body.requestId !== "string" || !/^audio:[0-9]+:[0-9]+(?::[A-Za-z0-9_-]{1,32})?$/.test(body.requestId) ||
    typeof body.chatId !== "string" || !/^[1-9][0-9]{0,19}$/.test(body.chatId) || body.language !== "ko" ||
    !body.source || typeof body.source !== "object" || Array.isArray(body.source)) throw new Error("INVALID_REQUEST");
  const source = body.source as Record<string, unknown>;
  if (Object.keys(source).some((key) => !["url", "declaredBytes", "expiresAtMillis", "declaredExtension"].includes(key)) ||
    typeof source.url !== "string" || source.url.length > 4_096 ||
    typeof source.declaredBytes !== "number" || !Number.isSafeInteger(source.declaredBytes) || source.declaredBytes <= 0 || source.declaredBytes > sourceMaxBytes ||
    typeof source.expiresAtMillis !== "number" || !Number.isSafeInteger(source.expiresAtMillis) || source.expiresAtMillis <= Date.now() ||
    !["mp3", "m4a", "wav"].includes(String(source.declaredExtension))) throw new Error("INVALID_SOURCE");
  const url = new URL(source.url);
  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "talk.kakaocdn.net" || url.username || url.password || (url.port && url.port !== "443")) throw new Error("FORBIDDEN_SOURCE");
  return { requestId: body.requestId, chatId: body.chatId, language: "ko", source: {
    url: source.url, declaredBytes: source.declaredBytes, expiresAtMillis: source.expiresAtMillis,
    declaredExtension: source.declaredExtension as "mp3" | "m4a" | "wav",
  } };
}

function statusFor(code: string): number {
  if (code === "BODY_TOO_LARGE") return 413;
  if (code.includes("QUEUE")) return 429;
  if (code.includes("FORBIDDEN")) return 403;
  if (code.includes("SCOPE")) return 409;
  return 400;
}

export function createAudioServer(config: AudioProxyConfig, processor: AudioProcessorPort): Server {
  const secret = readSecret(config.managerSecretFile);
  return createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") return json(response, 200, { ok: true, service: "proxy-audio" });
      if (request.method === "GET" && url.pathname === "/ready") {
        const ready = await processor.readiness();
        return json(response, ready.ready ? 200 : 503, ready);
      }
      if (!authenticate(request.headers.authorization, secret)) return json(response, 401, { error: { code: "UNAUTHORIZED" } });
      if (request.method === "POST" && url.pathname === "/v1/self-test/readiness") {
        const ready = await processor.readiness();
        return json(response, ready.ready ? 200 : 503, ready);
      }
      if (request.method === "POST" && url.pathname === "/v1/audio/transcriptions") {
        const result = processor.create(validate(await readJson(request, config.requestMaxBytes), config.sourceMaxBytes));
        return json(response, result.created ? 202 : 200, result.job);
      }
      const purge = url.pathname.match(/^\/v1\/audio\/transcriptions\/([0-9a-f-]+)\/purge$/);
      if (request.method === "DELETE" && purge) {
        const chatId = url.searchParams.get("chatId") ?? "";
        return processor.purge(purge[1]!, chatId)
          ? json(response, 200, { deleted: true })
          : json(response, 404, { error: { code: "JOB_NOT_FOUND" } });
      }
      const match = url.pathname.match(/^\/v1\/audio\/transcriptions\/([0-9a-f-]+)$/);
      if (match && (request.method === "GET" || request.method === "DELETE")) {
        const chatId = url.searchParams.get("chatId") ?? "";
        const job = request.method === "GET" ? processor.get(match[1]!, chatId) : processor.cancel(match[1]!, chatId);
        return job ? json(response, 200, job) : json(response, 404, { error: { code: "JOB_NOT_FOUND" } });
      }
      return json(response, 404, { error: { code: "NOT_FOUND" } });
    } catch (error) {
      const code = ((error as Error).message || "AUDIO_INTERNAL_ERROR").replace(/[^A-Z0-9_.-]/gi, "_").slice(0, 64);
      return json(response, statusFor(code), { error: { code } });
    }
  });
}
