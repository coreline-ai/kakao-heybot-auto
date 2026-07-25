import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { createServer, type Server } from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import { PNG } from "pngjs";
import { loadImageProxyConfig } from "../../src/config/config.js";
import { createImageServer, type ImageServerContext } from "../../src/http/server.js";

const root = mkdtempSync(resolve(tmpdir(), "proxy-image-test-"));
const managerSecret = "m".repeat(48);
const codexSecret = "c".repeat(48);
let imageContext: ImageServerContext;
let codexServer: Server;
let baseUrl: string;
let image: Buffer;

before(async () => {
  const png = new PNG({ width: 256, height: 256 });
  for (let y = 0; y < 256; y += 1) {
    for (let x = 0; x < 256; x += 1) {
      const offset = (y * 256 + x) * 4;
      png.data[offset] = x;
      png.data[offset + 1] = y;
      png.data[offset + 2] = (x + y) % 256;
      png.data[offset + 3] = 255;
    }
  }
  image = PNG.sync.write(png);
  const sha256 = createHash("sha256").update(image).digest("hex");
  codexServer = createServer(async (request, response) => {
    if (request.url === "/ready") {
      response.writeHead(200, { "content-type": "application/json" });
      return response.end(JSON.stringify({ ready: true }));
    }
    assert.equal(request.headers.authorization, `Bearer ${codexSecret}`);
    assert.equal(request.headers["x-heybot-service-id"], "image");
    if (request.method === "POST" && request.url === "/internal/v1/codex/jobs") {
      for await (const _ of request) {
        // Drain request.
      }
      response.writeHead(202, { "content-type": "application/json" });
      return response.end(
        JSON.stringify({
          jobId: "11111111-1111-4111-8111-111111111111",
          status: "succeeded",
          artifacts: [
            {
              artifactId: "22222222-2222-4222-8222-222222222222",
              mediaType: "image/png",
              bytes: image.length,
              sha256,
            },
          ],
        }),
      );
    }
    if (request.url?.includes("/artifacts/")) {
      response.writeHead(200, {
        "content-type": "image/png",
        "content-length": image.length,
      });
      return response.end(image);
    }
    response.writeHead(404).end();
  });
  await new Promise<void>((resolvePromise) =>
    codexServer.listen(0, "127.0.0.1", resolvePromise),
  );
  mkdirSync(resolve(root, "runtime/secrets"), { recursive: true });
  writeFileSync(resolve(root, "runtime/secrets/manager.secret"), managerSecret);
  writeFileSync(resolve(root, "runtime/secrets/codex.secret"), codexSecret);
  const codexPort = (codexServer.address() as AddressInfo).port;
  const config = loadImageProxyConfig(
    {
      IMAGE_PROXY_RUNTIME_DIR: "./runtime",
      IMAGE_PROXY_MANAGER_SECRET_FILE: "./runtime/secrets/manager.secret",
      IMAGE_PROXY_CODEX_SECRET_FILE: "./runtime/secrets/codex.secret",
      IMAGE_PROXY_CODEX_BASE_URL: `http://127.0.0.1:${codexPort}`,
      IMAGE_PROXY_IMAGE_MAX_BYTES: "2000000",
      IMAGE_PROXY_CODEX_JOB_TIMEOUT_MS: "5000",
    },
    root,
  );
  imageContext = createImageServer(config);
  await new Promise<void>((resolvePromise) =>
    imageContext.server.listen(0, "127.0.0.1", resolvePromise),
  );
  baseUrl = `http://127.0.0.1:${(imageContext.server.address() as AddressInfo).port}`;
});

after(async () => {
  await new Promise<void>((resolvePromise) =>
    imageContext.server.close(() => resolvePromise()),
  );
  await imageContext.shutdown();
  await new Promise<void>((resolvePromise) => codexServer.close(() => resolvePromise()));
});

function headers(): Record<string, string> {
  return {
    authorization: `Bearer ${managerSecret}`,
    "content-type": "application/json",
  };
}

test("preserves IDs, is idempotent, validates and downloads a PNG", async () => {
  const request = {
    requestId: "image-log-1726",
    chatId: "18480337854645134",
    userId: "7216943976749157453",
    logId: "900719925474099312",
    prompt: "분홍색 로봇",
  };
  const unauthorized = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(request),
  });
  assert.equal(unauthorized.status, 401);

  const created = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request),
  });
  assert.equal(created.status, 202);
  const initial = (await created.json()) as Record<string, any>;
  assert.equal(initial.chatId, "18480337854645134");

  const repeated = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request),
  });
  assert.equal(repeated.status, 200);
  assert.equal(((await repeated.json()) as any).jobId, initial.jobId);

  let completed: Record<string, any> | undefined;
  for (let attempt = 0; attempt < 100; attempt += 1) {
    const response = await fetch(
      `${baseUrl}/v1/image/jobs/${initial.jobId}?chatId=${request.chatId}`,
      {
      headers: headers(),
      },
    );
    const body = (await response.json()) as Record<string, any>;
    if (body.status === "succeeded") {
      completed = body;
      break;
    }
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 20));
  }
  assert.equal(completed?.status, "succeeded");
  const file = await fetch(
    `${baseUrl}/v1/image/jobs/${initial.jobId}/file?chatId=${request.chatId}`,
    { headers: headers() },
  );
  assert.equal(file.status, 200);
  assert.deepEqual(Buffer.from(await file.arrayBuffer()), image);
});

test("scopes status, file, cancel and idempotency to the exact chat ID", async () => {
  const request = {
    requestId: "image-room-scope-1",
    chatId: "18480337854645134",
    userId: "7216943976749157453",
    logId: "900719925474099313",
    prompt: "방 소유권 테스트",
  };
  const created = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify(request),
  });
  assert.equal(created.status, 202);
  const job = (await created.json()) as { jobId: string };
  const otherChatId = "18226456888539938";

  for (const [method, suffix] of [
    ["GET", ""],
    ["GET", "/file"],
    ["DELETE", ""],
  ] as const) {
    const response = await fetch(
      `${baseUrl}/v1/image/jobs/${job.jobId}${suffix}?chatId=${otherChatId}`,
      { method, headers: headers() },
    );
    assert.equal(response.status, 404);
    const body = JSON.stringify(await response.json());
    assert.doesNotMatch(body, new RegExp(job.jobId));
    assert.doesNotMatch(body, new RegExp(request.chatId));
  }

  const missingScope = await fetch(`${baseUrl}/v1/image/jobs/${job.jobId}`, {
    headers: headers(),
  });
  assert.equal(missingScope.status, 400);
  assert.equal(((await missingScope.json()) as any).error.code, "INVALID_CHAT_ID");

  const collidingRequest = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    headers: headers(),
    body: JSON.stringify({ ...request, chatId: otherChatId }),
  });
  assert.equal(collidingRequest.status, 404);
  const collisionBody = JSON.stringify(await collidingRequest.json());
  assert.doesNotMatch(collisionBody, new RegExp(job.jobId));
  assert.doesNotMatch(collisionBody, new RegExp(request.chatId));
});
