import { createReadStream, statSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import { authenticateBearer, readSecret } from "../auth/auth.js";
import type { ImageProxyConfig } from "../config/config.js";
import { CodexClient } from "../clients/codex/client.js";
import { ImageJobStore } from "../storage/store.js";
import { ImageJobProcessor } from "../orchestration/processor.js";
import { toPublicImageJob, type CreateImageJob } from "../jobs/types.js";

function json(response: ServerResponse, status: number, body: unknown): void {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": data.length,
    "cache-control": "no-store",
  });
  response.end(data);
}

async function readJson(request: IncomingMessage, maximumBytes: number): Promise<unknown> {
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > maximumBytes) throw new Error("BODY_TOO_LARGE");
    chunks.push(buffer);
  }
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8"));
  } catch {
    throw new Error("INVALID_JSON");
  }
}

function validateCreate(value: unknown, config: ImageProxyConfig): CreateImageJob {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_REQUEST");
  const body = value as Record<string, unknown>;
  const allowed = ["requestId", "chatId", "userId", "logId", "prompt"];
  if (Object.keys(body).some((key) => !allowed.includes(key))) throw new Error("UNSUPPORTED_FIELD");
  const id = /^[1-9]\d{0,19}$/;
  if (
    typeof body.requestId !== "string" ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId) ||
    typeof body.chatId !== "string" ||
    !id.test(body.chatId) ||
    typeof body.userId !== "string" ||
    !id.test(body.userId) ||
    typeof body.logId !== "string" ||
    !id.test(body.logId) ||
    typeof body.prompt !== "string" ||
    body.prompt.trim().length < 1 ||
    body.prompt.length > config.promptMaxChars
  ) {
    throw new Error("INVALID_REQUEST");
  }
  return {
    requestId: body.requestId,
    chatId: body.chatId,
    userId: body.userId,
    logId: body.logId,
    prompt: body.prompt.trim(),
  };
}

const DECIMAL_ID = /^[1-9]\d{0,19}$/;

function scopedJob(
  store: ImageJobStore,
  jobId: string,
  url: URL,
): ReturnType<ImageJobStore["get"]> {
  const chatId = url.searchParams.get("chatId");
  if (!chatId || !DECIMAL_ID.test(chatId)) throw new Error("INVALID_CHAT_ID");
  const job = store.get(jobId);
  return job?.chatId === chatId ? job : undefined;
}

export interface ImageServerContext {
  server: Server;
  store: ImageJobStore;
  processor: ImageJobProcessor;
  codex: CodexClient;
  shutdown(): Promise<void>;
}

export function createImageServer(config: ImageProxyConfig): ImageServerContext {
  const managerSecret = readSecret(config.managerSecretFile);
  const store = new ImageJobStore(config.databaseFile);
  const codex = new CodexClient(config);
  const processor = new ImageJobProcessor(store, codex, config);
  processor.start();

  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return json(response, 200, { ok: true, service: "proxy-image" });
      }
      if (request.method === "GET" && url.pathname === "/ready") {
        const dependency = await codex.readiness(AbortSignal.timeout(2_000));
        return json(response, dependency.ready ? 200 : 503, {
          ready: dependency.ready,
          dependency: { codex: dependency },
          queue: processor.snapshot(),
        });
      }
      if (!authenticateBearer(request.headers.authorization, managerSecret)) {
        return json(response, 401, { error: { code: "UNAUTHORIZED" } });
      }
      if (request.method === "POST" && url.pathname === "/v1/self-test/readiness") {
        const dependency = await codex.readiness(AbortSignal.timeout(2_000));
        return json(response, dependency.ready ? 200 : 503, {
          ready: dependency.ready,
          dependency: { codex: dependency },
        });
      }
      if (request.method === "POST" && url.pathname === "/v1/self-test/generate") {
        if (request.headers["x-confirm-cost"] !== "true") {
          return json(response, 412, { error: { code: "CANARY_CONFIRMATION_REQUIRED" } });
        }
        const requestId = `canary-${Date.now()}`;
        const job = store.create({
          requestId,
          chatId: "1",
          userId: "1",
          logId: String(Date.now()),
          prompt: "검증용: 밝은 배경의 작은 분홍색 로봇 아이콘",
        });
        processor.kick();
        return json(response, 202, toPublicImageJob(job));
      }
      if (request.method === "POST" && url.pathname === "/v1/image/jobs") {
        const input = validateCreate(await readJson(request, config.requestMaxBytes), config);
        const existing = store.findByRequest(input.requestId);
        if (existing) {
          if (existing.chatId !== input.chatId) {
            return json(response, 404, { error: { code: "IMAGE_JOB_NOT_FOUND" } });
          }
          return json(response, 200, toPublicImageJob(existing));
        }
        if (store.countPending() >= config.queueMaxPending) {
          return json(response, 429, {
            error: { code: "IMAGE_QUEUE_FULL", retryAfterMs: 5_000 },
          });
        }
        if (store.countRoomPending(input.chatId) >= config.queueMaxPendingPerRoom) {
          return json(response, 429, {
            error: { code: "ROOM_QUEUE_LIMIT", retryAfterMs: 5_000 },
          });
        }
        const job = store.create(input);
        processor.kick();
        return json(response, 202, toPublicImageJob(job));
      }
      const fileMatch = url.pathname.match(/^\/v1\/image\/jobs\/([0-9a-f-]+)\/file$/);
      if (request.method === "GET" && fileMatch) {
        const job = scopedJob(store, fileMatch[1]!, url);
        if (job?.status !== "succeeded" || !job.artifactPath) {
          return json(response, 404, { error: { code: "IMAGE_FILE_NOT_FOUND" } });
        }
        const bytes = statSync(job.artifactPath).size;
        response.writeHead(200, {
          "content-type": "image/png",
          "content-length": bytes,
          "cache-control": "no-store",
          "x-content-type-options": "nosniff",
        });
        createReadStream(job.artifactPath).pipe(response);
        return;
      }
      const jobMatch = url.pathname.match(/^\/v1\/image\/jobs\/([0-9a-f-]+)$/);
      if (jobMatch) {
        const job = scopedJob(store, jobMatch[1]!, url);
        if (!job) return json(response, 404, { error: { code: "IMAGE_JOB_NOT_FOUND" } });
        if (request.method === "GET") return json(response, 200, toPublicImageJob(job));
        if (request.method === "DELETE") {
          const cancelled = await processor.cancel(job.id);
          return json(response, cancelled ? 202 : 409, toPublicImageJob(store.get(job.id)!));
        }
      }
      return json(response, 404, { error: { code: "NOT_FOUND" } });
    } catch (error) {
      const code = (error as Error).message.slice(0, 64);
      const status = code === "BODY_TOO_LARGE" ? 413 : 400;
      return json(response, status, { error: { code } });
    }
  });
  let shutdownPromise: Promise<void> | undefined;
  const shutdown = (): Promise<void> => {
    if (!shutdownPromise) {
      shutdownPromise = processor.close().then(() => {
        store.close();
      });
    }
    return shutdownPromise;
  };
  server.on("close", () => {
    void shutdown();
  });
  return { server, store, processor, codex, shutdown };
}
