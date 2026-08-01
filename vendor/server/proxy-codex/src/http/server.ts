import { createReadStream, statSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import type { CodexProxyConfig } from "../config/config.js";
import { CodexAuthenticator } from "../auth/auth.js";
import { CapabilityRegistry } from "../capabilities/capabilities.js";
import { CodexJobStore } from "../jobs/store.js";
import { CodexJobProcessor } from "../queue/processor.js";
import { toPublicJob } from "../jobs/types.js";
import type { CodexRunner } from "../cli/runner.js";
import {
  CliCodexTextRunner,
  FakeCodexTextRunner,
  type CodexTextRunner,
} from "../cli/text-runner.js";
import type { CodexTextRequest } from "../conversation/types.js";
import { BoundedConversationQueue } from "../conversation/queue.js";
import {
  CliCodexVisionRunner,
  FakeCodexVisionRunner,
  type CodexVisionRunner,
} from "../vision/runner.js";

interface JobRequestBody {
  requestId: string;
  capability: "image.generate";
  input: { prompt: string };
  artifactContract?: {
    acceptedMediaTypes?: string[];
    maxArtifacts?: number;
    maxBytesPerArtifact?: number;
  };
}

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

async function readBytes(request: IncomingMessage, maximumBytes: number): Promise<Buffer> {
  const declared = Number(request.headers["content-length"] ?? 0);
  if (Number.isFinite(declared) && declared > maximumBytes) throw new Error("BODY_TOO_LARGE");
  const chunks: Buffer[] = [];
  let bytes = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > maximumBytes) throw new Error("BODY_TOO_LARGE");
    chunks.push(buffer);
  }
  if (bytes < 12) throw new Error("INVALID_IMAGE");
  return Buffer.concat(chunks);
}

function assertImageMagic(data: Buffer, mediaType: string): void {
  const png = data.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
  const jpeg = data[0] === 0xff && data[1] === 0xd8 && data.at(-2) === 0xff && data.at(-1) === 0xd9;
  const webp = data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WEBP";
  if ((mediaType === "image/png" && png) || (mediaType === "image/jpeg" && jpeg) || (mediaType === "image/webp" && webp)) return;
  throw new Error("INVALID_IMAGE");
}

function exactKeys(value: Record<string, unknown>, allowed: string[]): boolean {
  return Object.keys(value).every((key) => allowed.includes(key));
}

function validateJobBody(value: unknown, config: CodexProxyConfig): JobRequestBody {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_REQUEST");
  const body = value as Record<string, unknown>;
  if (!exactKeys(body, ["requestId", "capability", "input", "artifactContract"])) {
    throw new Error("UNSUPPORTED_FIELD");
  }
  if (
    typeof body.requestId !== "string" ||
    !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId) ||
    body.capability !== "image.generate"
  ) {
    throw new Error("INVALID_REQUEST");
  }
  if (!body.input || typeof body.input !== "object" || Array.isArray(body.input)) {
    throw new Error("INVALID_INPUT");
  }
  const input = body.input as Record<string, unknown>;
  if (
    !exactKeys(input, ["prompt"]) ||
    typeof input.prompt !== "string" ||
    input.prompt.trim().length < 1 ||
    input.prompt.length > 1_000
  ) {
    throw new Error("INVALID_PROMPT");
  }
  if (body.artifactContract !== undefined) {
    if (
      !body.artifactContract ||
      typeof body.artifactContract !== "object" ||
      Array.isArray(body.artifactContract)
    ) {
      throw new Error("INVALID_ARTIFACT_CONTRACT");
    }
    const contract = body.artifactContract as Record<string, unknown>;
    if (
      !exactKeys(contract, ["acceptedMediaTypes", "maxArtifacts", "maxBytesPerArtifact"]) ||
      (contract.maxArtifacts !== undefined && contract.maxArtifacts !== 1) ||
      (contract.acceptedMediaTypes !== undefined &&
        (JSON.stringify(contract.acceptedMediaTypes) !== JSON.stringify(["image/png"]))) ||
      (typeof contract.maxBytesPerArtifact === "number" &&
        contract.maxBytesPerArtifact > config.artifactMaxBytes)
    ) {
      throw new Error("INVALID_ARTIFACT_CONTRACT");
    }
  }
  return {
    requestId: body.requestId,
    capability: body.capability,
    input: { prompt: input.prompt.trim() },
    artifactContract: body.artifactContract as JobRequestBody["artifactContract"],
  };
}

function statusForError(code: string): number {
  if (code === "BODY_TOO_LARGE") return 413;
  if (code.includes("FORBIDDEN")) return 403;
  if (code.includes("DISABLED")) return 503;
  return 400;
}

export interface CodexServerContext {
  server: Server;
  processor: CodexJobProcessor;
  store: CodexJobStore;
  runner: CodexRunner;
  shutdown(): Promise<void>;
}

export function createCodexServer(
  config: CodexProxyConfig,
  runner: CodexRunner,
  textRunner: CodexTextRunner = config.runnerMode === "fake"
    ? new FakeCodexTextRunner()
    : new CliCodexTextRunner(config),
  visionRunner: CodexVisionRunner = config.runnerMode === "fake"
    ? new FakeCodexVisionRunner()
    : new CliCodexVisionRunner(config),
): CodexServerContext {
  const auth = new CodexAuthenticator(config.managerSecretFile, config.callerSecretsDir);
  const capabilities = new CapabilityRegistry(config.capabilitiesFile);
  const store = new CodexJobStore(config.databaseFile);
  const processor = new CodexJobProcessor(store, runner, config);
  const textQueue = new BoundedConversationQueue(
    config.textQueueConcurrency,
    config.textQueueMaxPending,
  );
  const visionQueue = new BoundedConversationQueue(
    config.visionQueueConcurrency,
    config.visionQueueMaxPending,
  );
  let readiness: Awaited<ReturnType<CodexRunner["readiness"]>> = {
    ready: false,
    reason: "STARTING",
  };
  void runner.readiness().then((value) => {
    readiness = value;
  });
  processor.start();

  const server = createServer(async (request, response) => {
    const url = new URL(request.url ?? "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") {
        return json(response, 200, { ok: true, service: "proxy-codex" });
      }
      if (request.method === "GET" && url.pathname === "/ready") {
        return json(response, readiness.ready ? 200 : 503, {
          ready: readiness.ready,
          reason: readiness.reason,
          version: readiness.version,
          queue: processor.snapshot(),
          text: textQueue.snapshot(),
          vision: visionQueue.snapshot(),
        });
      }

      const caller = request.headers["x-heybot-service-id"];
      const callerId = typeof caller === "string" ? caller : "";
      const isManagerPath = url.pathname.startsWith("/internal/v1/self-test/");
      const authenticated = isManagerPath
        ? auth.authenticateManager(request.headers.authorization)
        : auth.authenticateCaller(callerId, request.headers.authorization);
      if (!authenticated) return json(response, 401, { error: { code: "UNAUTHORIZED" } });

      if (
        request.method === "POST" &&
        url.pathname === "/internal/v1/self-test/readiness"
      ) {
        readiness = await runner.readiness();
        return json(response, readiness.ready ? 200 : 503, readiness);
      }
      const canaryMatch = url.pathname.match(
        /^\/internal\/v1\/self-test\/capabilities\/([a-z0-9.]+)$/,
      );
      if (request.method === "POST" && canaryMatch) {
        if (request.headers["x-confirm-cost"] !== "true") {
          return json(response, 412, {
            error: { code: "CANARY_CONFIRMATION_REQUIRED" },
          });
        }
        const capability = capabilities
          .list()
          .find((entry) => entry.id === canaryMatch[1] && entry.enabled);
        if (!capability) {
          return json(response, 404, { error: { code: "CAPABILITY_NOT_FOUND" } });
        }
        if (store.countPending() >= config.queueMaxPending) {
          return json(response, 429, {
            error: { code: "CODEX_QUEUE_FULL", retryAfterMs: 5_000 },
          });
        }
        const job = store.createOrGet({
          caller: "manager-canary",
          requestId: `canary-${Date.now()}`,
          capability: "image.generate",
          prompt: "검증용: 밝은 배경의 작은 분홍색 로봇 아이콘",
        }).job;
        processor.kick();
        return json(response, 202, toPublicJob(job));
      }

      if (request.method === "POST" && url.pathname === "/internal/v1/codex/jobs") {
        capabilities.requireAllowed("image.generate", callerId);
        const body = validateJobBody(await readJson(request, config.requestMaxBytes), config);
        const existing = store.findByRequest(callerId, body.requestId);
        if (existing) return json(response, 200, toPublicJob(existing));
        if (store.countPending() >= config.queueMaxPending) {
          return json(response, 429, {
            error: { code: "CODEX_QUEUE_FULL", retryAfterMs: 5_000 },
          });
        }
        const result = store.createOrGet({
          caller: callerId,
          requestId: body.requestId,
          capability: body.capability,
          prompt: body.input.prompt,
        });
        processor.kick();
        return json(response, result.created ? 202 : 200, toPublicJob(result.job));
      }

      if (
        request.method === "POST" &&
        url.pathname === "/internal/v1/codex/conversation"
      ) {
        capabilities.requireAllowed("conversation.respond.v1", callerId);
        const value = await readJson(request, config.requestMaxBytes);
        if (!value || typeof value !== "object" || Array.isArray(value)) {
          throw new Error("INVALID_REQUEST");
        }
        const body = value as Record<string, unknown>;
        if (
          Object.keys(body).some((key) => !["requestId", "capability", "input"].includes(key)) ||
          body.capability !== "conversation.respond.v1" ||
          typeof body.requestId !== "string" ||
          !/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId)
        ) {
          throw new Error("INVALID_REQUEST");
        }
        const input = body.input;
        if (!input || typeof input !== "object" || Array.isArray(input)) {
          throw new Error("INVALID_INPUT");
        }
        const rawMessages = (input as Record<string, unknown>).messages;
        if (!Array.isArray(rawMessages) || rawMessages.length < 1 || rawMessages.length > 32) {
          throw new Error("INVALID_MESSAGES");
        }
        const messages = rawMessages.map((item) => {
          if (!item || typeof item !== "object" || Array.isArray(item)) {
            throw new Error("INVALID_MESSAGE");
          }
          const message = item as Record<string, unknown>;
          if (
            Object.keys(message).some((key) => !["role", "content"].includes(key)) ||
            !["system", "user", "assistant"].includes(String(message.role)) ||
            typeof message.content !== "string" ||
            message.content.trim().length < 1 ||
            message.content.length > 4_000
          ) {
            throw new Error("INVALID_MESSAGE");
          }
          return {
            role: message.role as CodexTextRequest["messages"][number]["role"],
            content: message.content.trim(),
          };
        });
        const result = await textQueue.run(() =>
          textRunner.run(
            { requestId: body.requestId as string, messages },
            AbortSignal.timeout(config.textTimeoutMs),
          ),
        );
        return json(response, 200, {
          requestId: result.requestId,
          engine: "codex",
          text: result.text,
          latencyMillis: result.latencyMillis,
        });
      }

      if (
        request.method === "POST" &&
        url.pathname === "/internal/v1/codex/vision/analyze"
      ) {
        capabilities.requireAllowed("image.analyze.v1", callerId);
        const requestId = request.headers["x-request-id"];
        if (typeof requestId !== "string" || !/^[A-Za-z0-9._:-]{1,128}$/.test(requestId)) {
          throw new Error("INVALID_REQUEST_ID");
        }
        const mediaType = String(request.headers["content-type"] ?? "").split(";", 1)[0]!;
        if (!["image/png", "image/jpeg", "image/webp"].includes(mediaType)) {
          throw new Error("UNSUPPORTED_MEDIA_TYPE");
        }
        const source = await readBytes(request, config.visionMaxInputBytes);
        assertImageMagic(source, mediaType);
        const result = await visionQueue.run(() =>
          visionRunner.run(
            requestId,
            source,
            mediaType,
            AbortSignal.timeout(config.visionTimeoutMs),
          ),
        );
        return json(response, 200, { requestId, result });
      }

      const artifactMatch = url.pathname.match(
        /^\/internal\/v1\/codex\/jobs\/([0-9a-f-]+)\/artifacts\/([0-9a-f-]+)$/,
      );
      if (request.method === "GET" && artifactMatch) {
        const job = store.get(artifactMatch[1]!);
        if (
          !job ||
          job.caller !== callerId ||
          job.status !== "succeeded" ||
          job.artifactId !== artifactMatch[2] ||
          !job.artifactPath
        ) {
          return json(response, 404, { error: { code: "ARTIFACT_NOT_FOUND" } });
        }
        const size = statSync(job.artifactPath).size;
        response.writeHead(200, {
          "content-type": "image/png",
          "content-length": size,
          "cache-control": "no-store",
          "x-content-type-options": "nosniff",
        });
        createReadStream(job.artifactPath).pipe(response);
        return;
      }

      const jobMatch = url.pathname.match(/^\/internal\/v1\/codex\/jobs\/([0-9a-f-]+)$/);
      if (jobMatch) {
        const job = store.get(jobMatch[1]!);
        if (!job || job.caller !== callerId) {
          return json(response, 404, { error: { code: "JOB_NOT_FOUND" } });
        }
        if (request.method === "GET") return json(response, 200, toPublicJob(job));
        if (request.method === "DELETE") {
          const cancelled = processor.cancel(job.id);
          return json(response, cancelled ? 202 : 409, {
            jobId: job.id,
            status: store.get(job.id)?.status,
          });
        }
      }
      return json(response, 404, { error: { code: "NOT_FOUND" } });
    } catch (error) {
      const code = (error as Error).message.slice(0, 64);
      return json(response, statusForError(code), { error: { code } });
    }
  });

  let shutdownPromise: Promise<void> | undefined;
  const shutdown = (): Promise<void> => {
    if (!shutdownPromise) {
      textQueue.close();
      visionQueue.close();
      shutdownPromise = processor.close().then(() => {
        store.close();
      });
    }
    return shutdownPromise;
  };
  server.on("close", () => {
    void shutdown();
  });
  return { server, processor, store, runner, shutdown };
}
