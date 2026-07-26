import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { loadImageProxyConfig } from "../../src/config/config.js";
import { createImageServer } from "../../src/http/server.js";

async function closeServer(server: Server): Promise<void> {
  server.closeIdleConnections();
  server.closeAllConnections();
  await new Promise<void>((resolvePromise, reject) =>
    server.close((error) => (error ? reject(error) : resolvePromise())),
  );
}

test("enforces per-room and total pending limits while a Codex job is running", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "proxy-image-limits-"));
  const managerSecret = "m".repeat(48);
  const codexSecret = "c".repeat(48);
  let codexSequence = 0;
  const codex: Server = createServer(async (request, response) => {
    if (request.url === "/ready") {
      response.writeHead(200, { "content-type": "application/json" });
      return response.end(JSON.stringify({ ready: true }));
    }
    if (request.method === "POST" && request.url === "/internal/v1/codex/jobs") {
      for await (const _ of request) {
        // Drain request.
      }
      codexSequence += 1;
      response.writeHead(202, { "content-type": "application/json" });
      return response.end(
        JSON.stringify({
          jobId: `11111111-1111-4111-8111-${String(codexSequence).padStart(12, "0")}`,
          status: "running",
          artifacts: [],
        }),
      );
    }
    if (request.method === "GET" && request.url?.includes("/codex/jobs/")) {
      response.writeHead(200, { "content-type": "application/json" });
      return response.end(JSON.stringify({ status: "running", artifacts: [] }));
    }
    if (request.method === "DELETE") {
      response.writeHead(202, { "content-type": "application/json" });
      return response.end(JSON.stringify({ status: "cancelled" }));
    }
    response.writeHead(404).end();
  });
  await new Promise<void>((resolvePromise) =>
    codex.listen(0, "127.0.0.1", resolvePromise),
  );

  mkdirSync(resolve(root, "runtime/secrets"), { recursive: true });
  writeFileSync(resolve(root, "runtime/secrets/manager.secret"), managerSecret);
  writeFileSync(resolve(root, "runtime/secrets/codex.secret"), codexSecret);
  const config = loadImageProxyConfig(
    {
      IMAGE_PROXY_RUNTIME_DIR: "./runtime",
      IMAGE_PROXY_MANAGER_SECRET_FILE: "./runtime/secrets/manager.secret",
      IMAGE_PROXY_CODEX_SECRET_FILE: "./runtime/secrets/codex.secret",
      IMAGE_PROXY_CODEX_BASE_URL: `http://127.0.0.1:${
        (codex.address() as AddressInfo).port
      }`,
      IMAGE_PROXY_QUEUE_MAX_PENDING: "2",
      IMAGE_PROXY_QUEUE_MAX_PENDING_PER_ROOM: "1",
      IMAGE_PROXY_CODEX_JOB_TIMEOUT_MS: "5000",
    },
    root,
  );
  const image = createImageServer(config);
  await new Promise<void>((resolvePromise) =>
    image.server.listen(0, "127.0.0.1", resolvePromise),
  );
  const base = `http://127.0.0.1:${(image.server.address() as AddressInfo).port}`;
  const headers = {
    authorization: `Bearer ${managerSecret}`,
    "content-type": "application/json",
  };
  const request = (requestId: string, chatId: string) => ({
    requestId,
    chatId,
    userId: "7216943976749157453",
    logId: requestId.replace(/\D/g, "") || "1",
    prompt: requestId,
  });

  try {
    const first = await fetch(`${base}/v1/image/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(request("request-1", "18480337854645134")),
    });
    assert.equal(first.status, 202);

    const sameRoom = await fetch(`${base}/v1/image/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(request("request-2", "18480337854645134")),
    });
    assert.equal(sameRoom.status, 429);
    assert.equal(((await sameRoom.json()) as any).error.code, "ROOM_QUEUE_LIMIT");

    const secondRoom = await fetch(`${base}/v1/image/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(request("request-3", "18226456888539938")),
    });
    assert.equal(secondRoom.status, 202);

    const full = await fetch(`${base}/v1/image/jobs`, {
      method: "POST",
      headers,
      body: JSON.stringify(request("request-4", "18243496625741211")),
    });
    assert.equal(full.status, 429);
    assert.equal(((await full.json()) as any).error.code, "IMAGE_QUEUE_FULL");
  } finally {
    await closeServer(image.server);
    await image.shutdown();
    await closeServer(codex);
  }
});
