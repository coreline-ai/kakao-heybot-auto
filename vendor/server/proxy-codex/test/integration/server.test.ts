import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { loadCodexProxyConfig } from "../../src/config/config.js";
import { FakeCodexRunner, type CodexRunner, type RawArtifact } from "../../src/cli/runner.js";
import { createCodexServer, type CodexServerContext } from "../../src/http/server.js";
import type { CodexJob } from "../../src/jobs/types.js";

const root = mkdtempSync(resolve(tmpdir(), "proxy-codex-test-"));
const secret = "c".repeat(48);
const managerSecret = "m".repeat(48);
let context: CodexServerContext;
let baseUrl: string;

async function closeServer(server: Server): Promise<void> {
  server.closeIdleConnections();
  server.closeAllConnections();
  await new Promise<void>((resolvePromise, reject) =>
    server.close((error) => (error ? reject(error) : resolvePromise())),
  );
}

before(async () => {
  mkdirSync(resolve(root, "runtime/secrets/callers"), { recursive: true });
  mkdirSync(resolve(root, "config"), { recursive: true });
  writeFileSync(resolve(root, "runtime/secrets/manager.secret"), managerSecret);
  writeFileSync(resolve(root, "runtime/secrets/callers/image.secret"), secret);
  writeFileSync(resolve(root, "runtime/secrets/callers/vision.secret"), secret);
  writeFileSync(
    resolve(root, "config/capabilities.json"),
    JSON.stringify({
      schemaVersion: 1,
      capabilities: [
        {
          id: "image.generate",
          enabled: true,
          allowedCallers: ["image"],
          timeoutMs: 5_000,
          maxArtifacts: 1,
          acceptedMediaTypes: ["image/png"],
          maxBytesPerArtifact: 2_000_000,
        },
        {
          id: "image.analyze.v1",
          enabled: true,
          allowedCallers: ["vision"],
          timeoutMs: 5_000,
          maxArtifacts: 0,
          acceptedMediaTypes: ["image/png", "image/jpeg", "image/webp"],
          maxBytesPerArtifact: 0,
        },
      ],
    }),
  );
  const config = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNNER: "fake",
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_PROXY_JOB_TIMEOUT_MS: "5000",
      CODEX_PROXY_ARTIFACT_MAX_BYTES: "2000000",
      CODEX_PROXY_CAPABILITIES_FILE: "./config/capabilities.json",
      CODEX_PROXY_MANAGER_SECRET_FILE: "./runtime/secrets/manager.secret",
      CODEX_PROXY_CALLER_SECRETS_DIR: "./runtime/secrets/callers",
    },
    root,
  );
  context = createCodexServer(config, new FakeCodexRunner(config));
  await new Promise<void>((resolvePromise) =>
    context.server.listen(0, "127.0.0.1", resolvePromise),
  );
  baseUrl = `http://127.0.0.1:${(context.server.address() as AddressInfo).port}`;
});

after(async () => {
  await closeServer(context.server);
  await context.shutdown();
});

function headers(): Record<string, string> {
  return {
    authorization: `Bearer ${secret}`,
    "x-heybot-service-id": "image",
    "content-type": "application/json",
  };
}

async function waitForSuccess(jobId: string): Promise<Record<string, any>> {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    const response = await fetch(`${baseUrl}/internal/v1/codex/jobs/${jobId}`, {
      headers: headers(),
    });
    const body = (await response.json()) as Record<string, any>;
    if (body.status === "succeeded") return body;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 20));
  }
  throw new Error("job did not finish");
}

test("auth, capability schema, idempotency and PNG artifact contract", async () => {
  const request = {
    requestId: "image-request-1",
    capability: "image.generate",
    input: { prompt: "밝은 분홍색 로봇" },
    artifactContract: {
      acceptedMediaTypes: ["image/png"],
      maxArtifacts: 1,
      maxBytesPerArtifact: 2_000_000,
    },
  };
  const unauthorized = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    body: JSON.stringify(request),
    headers: { "content-type": "application/json" },
  });
  assert.equal(unauthorized.status, 401);

  const created = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    body: JSON.stringify(request),
    headers: headers(),
  });
  assert.equal(created.status, 202);
  const first = (await created.json()) as Record<string, any>;
  const repeated = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    body: JSON.stringify(request),
    headers: headers(),
  });
  assert.equal(repeated.status, 200);
  assert.equal(((await repeated.json()) as Record<string, any>).jobId, first.jobId);

  const completed = await waitForSuccess(first.jobId);
  assert.equal(completed.artifacts.length, 1);
  const artifact = completed.artifacts[0];
  const file = await fetch(
    `${baseUrl}/internal/v1/codex/jobs/${first.jobId}/artifacts/${artifact.artifactId}`,
    { headers: headers() },
  );
  assert.equal(file.status, 200);
  assert.equal(file.headers.get("content-type"), "image/png");
  const bytes = Buffer.from(await file.arrayBuffer());
  assert.deepEqual([...bytes.subarray(0, 8)], [137, 80, 78, 71, 13, 10, 26, 10]);
});

test("vision capability accepts only authenticated image bytes and returns strict JSON", async () => {
  const png = Buffer.alloc(24);
  Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]).copy(png);
  const response = await fetch(`${baseUrl}/internal/v1/codex/vision/analyze`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${secret}`,
      "x-heybot-service-id": "vision",
      "x-request-id": "vision-1",
      "content-type": "image/png",
    },
    body: png,
  });
  assert.equal(response.status, 200);
  const body = await response.json() as any;
  assert.equal(body.result.version, 1);
  assert.equal(body.result.uncertainty, "low");

  const invalid = await fetch(`${baseUrl}/internal/v1/codex/vision/analyze`, {
    method: "POST",
    headers: {
      authorization: `Bearer ${secret}`,
      "x-heybot-service-id": "vision",
      "x-request-id": "vision-2",
      "content-type": "image/png",
    },
    body: Buffer.from("not-an-image"),
  });
  assert.equal(invalid.status, 400);
});

test("rejects arbitrary execution fields and unauthorized caller", async () => {
  const invalid = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      requestId: "unsafe-1",
      capability: "image.generate",
      input: { prompt: "test" },
      argv: ["sh", "-c", "id"],
    }),
  });
  assert.equal(invalid.status, 400);
  assert.equal(((await invalid.json()) as any).error.code, "UNSUPPORTED_FIELD");

  const caller = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: {
      ...headers(),
      "x-heybot-service-id": "video",
    },
    body: JSON.stringify({
      requestId: "caller-1",
      capability: "image.generate",
      input: { prompt: "test" },
    }),
  });
  assert.equal(caller.status, 401);
});

test("enforces body and prompt boundaries", async () => {
  const request = (requestId: string, prompt: string) => ({
    requestId,
    capability: "image.generate",
    input: { prompt },
  });

  const empty = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request("prompt-empty", "   ")),
  });
  assert.equal(empty.status, 400);
  assert.equal(((await empty.json()) as any).error.code, "INVALID_PROMPT");

  const tooLong = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request("prompt-too-long", "가".repeat(1_001))),
  });
  assert.equal(tooLong.status, 400);
  assert.equal(((await tooLong.json()) as any).error.code, "INVALID_PROMPT");

  const maximum = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request("prompt-maximum", "가".repeat(1_000))),
  });
  assert.equal(maximum.status, 202);

  const oversized = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({
      ...request("body-too-large", "test"),
      padding: "x".repeat(33_000),
    }),
  });
  assert.equal(oversized.status, 413);
  assert.equal(((await oversized.json()) as any).error.code, "BODY_TOO_LARGE");
});

test("manager canary requires explicit cost confirmation and queues capability", async () => {
  const endpoint = `${baseUrl}/internal/v1/self-test/capabilities/image.generate`;
  const denied = await fetch(endpoint, {
    method: "POST",
    headers: { authorization: `Bearer ${managerSecret}` },
  });
  assert.equal(denied.status, 412);

  const accepted = await fetch(endpoint, {
    method: "POST",
    headers: {
      authorization: `Bearer ${managerSecret}`,
      "x-confirm-cost": "true",
    },
  });
  assert.equal(accepted.status, 202);
  const body = (await accepted.json()) as { jobId: string; status: string };
  assert.match(body.jobId, /^[0-9a-f-]+$/);
  assert.ok(["queued", "running", "succeeded"].includes(body.status));
});

test("enforces queue capacity but preserves idempotent lookup", async () => {
  class BlockingRunner implements CodexRunner {
    async readiness(): Promise<{ ready: boolean }> {
      return { ready: true };
    }

    run(_job: CodexJob, signal: AbortSignal): Promise<RawArtifact> {
      return new Promise((_resolvePromise, reject) => {
        signal.addEventListener("abort", () => reject(new Error("JOB_CANCELLED")), {
          once: true,
        });
      });
    }
  }

  const isolatedRoot = mkdtempSync(resolve(tmpdir(), "proxy-codex-limit-test-"));
  mkdirSync(resolve(isolatedRoot, "runtime/secrets/callers"), { recursive: true });
  mkdirSync(resolve(isolatedRoot, "config"), { recursive: true });
  writeFileSync(resolve(isolatedRoot, "runtime/secrets/manager.secret"), managerSecret);
  writeFileSync(resolve(isolatedRoot, "runtime/secrets/callers/image.secret"), secret);
  writeFileSync(
    resolve(isolatedRoot, "config/capabilities.json"),
    JSON.stringify({
      schemaVersion: 1,
      capabilities: [
        {
          id: "image.generate",
          enabled: true,
          allowedCallers: ["image"],
          timeoutMs: 5_000,
          maxArtifacts: 1,
          acceptedMediaTypes: ["image/png"],
          maxBytesPerArtifact: 2_000_000,
        },
      ],
    }),
  );
  const isolatedConfig = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_PROXY_RUNNER: "fake",
      CODEX_PROXY_QUEUE_MAX_PENDING: "1",
      CODEX_PROXY_CAPABILITIES_FILE: "./config/capabilities.json",
      CODEX_PROXY_MANAGER_SECRET_FILE: "./runtime/secrets/manager.secret",
      CODEX_PROXY_CALLER_SECRETS_DIR: "./runtime/secrets/callers",
    },
    isolatedRoot,
  );
  const isolated = createCodexServer(isolatedConfig, new BlockingRunner());
  await new Promise<void>((resolvePromise) =>
    isolated.server.listen(0, "127.0.0.1", resolvePromise),
  );
  const isolatedUrl = `http://127.0.0.1:${
    (isolated.server.address() as AddressInfo).port
  }`;
  const firstRequest = {
    requestId: "capacity-first",
    capability: "image.generate",
    input: { prompt: "first" },
  };

  try {
    const first = await fetch(`${isolatedUrl}/internal/v1/codex/jobs`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(firstRequest),
    });
    assert.equal(first.status, 202);
    const firstBody = (await first.json()) as { jobId: string };

    const repeated = await fetch(`${isolatedUrl}/internal/v1/codex/jobs`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify(firstRequest),
    });
    assert.equal(repeated.status, 200);
    assert.equal(((await repeated.json()) as { jobId: string }).jobId, firstBody.jobId);

    const full = await fetch(`${isolatedUrl}/internal/v1/codex/jobs`, {
      method: "POST",
      headers: headers(),
      body: JSON.stringify({
        requestId: "capacity-second",
        capability: "image.generate",
        input: { prompt: "second" },
      }),
    });
    assert.equal(full.status, 429);
    assert.equal(((await full.json()) as any).error.code, "CODEX_QUEUE_FULL");

    const cancelled = await fetch(
      `${isolatedUrl}/internal/v1/codex/jobs/${firstBody.jobId}`,
      { method: "DELETE", headers: headers() },
    );
    assert.equal(cancelled.status, 202);
  } finally {
    await closeServer(isolated.server);
    await isolated.shutdown();
  }
});
