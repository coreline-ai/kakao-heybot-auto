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
): CodexServerContext {
  const auth = new CodexAuthenticator(config.managerSecretFile, config.callerSecretsDir);
  const capabilities = new CapabilityRegistry(config.capabilitiesFile);
  const store = new CodexJobStore(config.databaseFile);
  const processor = new CodexJobProcessor(store, runner, config);
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
