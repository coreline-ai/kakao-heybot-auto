import assert from "node:assert/strict";
import { createServer, type Server } from "node:http";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { after, before, test } from "node:test";
import { loadManagerConfig } from "../../src/config/config.js";
import { createManagerServer } from "../../src/http/server.js";
import type {
  LifecycleAction,
  LifecycleController,
} from "../../src/lifecycle/launchd.js";

const root = mkdtempSync(resolve(tmpdir(), "proxy-manager-test-"));
const routeSecret = "r".repeat(48);
const adminSecret = "a".repeat(48);
const upstreamSecret = "u".repeat(48);
let upstream: Server;
let manager: Server;
let baseUrl: string;
const binary = Buffer.alloc(1024 * 1024, 42);

async function closeServer(server: Server): Promise<void> {
  // Node's fetch keeps HTTP sockets alive. Close them before awaiting close(),
  // otherwise node --test can remain alive after every assertion has finished.
  server.closeIdleConnections();
  server.closeAllConnections();
  await new Promise<void>((resolvePromise, reject) =>
    server.close((error) => (error ? reject(error) : resolvePromise())),
  );
}

before(async () => {
  upstream = createServer(async (request, response) => {
    if (request.url === "/health" || request.url === "/ready") {
      response.writeHead(200, { "content-type": "application/json" });
      return response.end(JSON.stringify({ ready: true }));
    }
    assert.equal(request.headers.authorization, `Bearer ${upstreamSecret}`);
    assert.equal(request.headers["x-heybot-service-id"], "manager");
    if (request.url?.endsWith("/file")) {
      response.writeHead(200, {
        "content-type": "image/png",
        "content-length": binary.length,
      });
      response.write(binary.subarray(0, binary.length / 2));
      return setImmediate(() => response.end(binary.subarray(binary.length / 2)));
    }
    const chunks: Buffer[] = [];
    for await (const chunk of request) chunks.push(Buffer.from(chunk));
    response.writeHead(200, { "content-type": "application/json" });
    response.end(Buffer.concat(chunks));
  });
  await new Promise<void>((resolvePromise) =>
    upstream.listen(0, "127.0.0.1", resolvePromise),
  );
  const upstreamPort = (upstream.address() as AddressInfo).port;
  mkdirSync(resolve(root, "runtime/secrets"), { recursive: true });
  mkdirSync(resolve(root, "config"), { recursive: true });
  writeFileSync(resolve(root, "runtime/secrets/route.secret"), routeSecret);
  writeFileSync(resolve(root, "runtime/secrets/admin.secret"), adminSecret);
  writeFileSync(resolve(root, "runtime/secrets/image.secret"), upstreamSecret);
  writeFileSync(
    resolve(root, "config/proxies.json"),
    JSON.stringify({
      schemaVersion: 1,
      proxies: [
        {
          id: "image",
          enabled: true,
          exposure: "gateway",
          routePrefix: "/v1/image",
          targetBaseUrl: `http://127.0.0.1:${upstreamPort}`,
          healthPath: "/health",
          readyPath: "/ready",
          readinessTestPath: "/ready",
          canaryTestPath: "/canary",
          canaryRequiresExplicitConfirmation: true,
          managerClientSecretFile: "./runtime/secrets/image.secret",
          dependencies: [],
        },
      ],
    }),
  );
  const config = loadManagerConfig(
    {
      MANAGER_ROUTE_SECRET_FILE: "./runtime/secrets/route.secret",
      MANAGER_ADMIN_SECRET_FILE: "./runtime/secrets/admin.secret",
      MANAGER_PROXY_REGISTRY_FILE: "./config/proxies.json",
    },
    root,
  );
  manager = createManagerServer(config).server;
  await new Promise<void>((resolvePromise) =>
    manager.listen(0, "127.0.0.1", resolvePromise),
  );
  baseUrl = `http://127.0.0.1:${(manager.address() as AddressInfo).port}`;
});

after(async () => {
  await closeServer(manager);
  await closeServer(upstream);
});

test("route auth replaces credentials and preserves 18-digit IDs", async () => {
  const payload = {
    requestId: "id-1",
    chatId: "18480337854645134",
    userId: "7216943976749157453",
    logId: "900719925474099312",
    prompt: "test",
  };
  const denied = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    body: JSON.stringify(payload),
    headers: { "content-type": "application/json" },
  });
  assert.equal(denied.status, 401);

  const accepted = await fetch(`${baseUrl}/v1/image/jobs`, {
    method: "POST",
    body: JSON.stringify(payload),
    headers: {
      "content-type": "application/json",
      authorization: `Bearer ${routeSecret}`,
    },
  });
  assert.equal(accepted.status, 200);
  assert.deepEqual(await accepted.json(), payload);
});

test("streams binary responses without base64 transformation", async () => {
  const response = await fetch(`${baseUrl}/v1/image/jobs/job-1/file`, {
    headers: { authorization: `Bearer ${routeSecret}` },
  });
  assert.equal(response.status, 200);
  assert.equal(response.headers.get("content-type"), "image/png");
  assert.deepEqual(Buffer.from(await response.arrayBuffer()), binary);
});

test("internal routes are not exposed and admin auth is separate", async () => {
  const internal = await fetch(`${baseUrl}/internal/v1/codex/jobs`, {
    headers: { authorization: `Bearer ${routeSecret}` },
  });
  assert.equal(internal.status, 404);

  const routeAsAdmin = await fetch(`${baseUrl}/manager/v1/proxies`, {
    headers: { authorization: `Bearer ${routeSecret}` },
  });
  assert.equal(routeAsAdmin.status, 401);
  const admin = await fetch(`${baseUrl}/manager/v1/proxies`, {
    headers: { authorization: `Bearer ${adminSecret}` },
  });
  assert.equal(admin.status, 200);
  const body = (await admin.json()) as Record<string, any>;
  assert.equal(body.proxies[0].managerClientSecretFile, undefined);
});

test("aggregates readiness", async () => {
  const response = await fetch(`${baseUrl}/ready`);
  assert.equal(response.status, 200);
  assert.equal(((await response.json()) as any).ready, true);
});

test("lifecycle is protected by admin auth and disabled by default", async () => {
  const response = await fetch(`${baseUrl}/manager/v1/proxies/image/restart`, {
    method: "POST",
    headers: { authorization: `Bearer ${adminSecret}` },
  });
  assert.equal(response.status, 403);
  assert.equal(((await response.json()) as any).error.code, "LIFECYCLE_DISABLED");
});

test("enabled lifecycle dispatches only the registry launchd label", async () => {
  const calls: Array<{ label: string; action: LifecycleAction }> = [];
  const controller: LifecycleController = {
    async run(label, action) {
      calls.push({ label, action });
    },
  };
  const document = JSON.parse(
    await import("node:fs/promises").then((fs) =>
      fs.readFile(resolve(root, "config/proxies.json"), "utf8"),
    ),
  );
  document.proxies[0].launchdLabel = "ai.coreline.heybot.proxy-image";
  writeFileSync(resolve(root, "config/proxies-lifecycle.json"), JSON.stringify(document));
  const lifecycleConfig = loadManagerConfig(
    {
      MANAGER_ROUTE_SECRET_FILE: "./runtime/secrets/route.secret",
      MANAGER_ADMIN_SECRET_FILE: "./runtime/secrets/admin.secret",
      MANAGER_PROXY_REGISTRY_FILE: "./config/proxies-lifecycle.json",
      MANAGER_LIFECYCLE_ENABLED: "true",
    },
    root,
  );
  const lifecycleServer = createManagerServer(lifecycleConfig, controller).server;
  await new Promise<void>((resolvePromise) =>
    lifecycleServer.listen(0, "127.0.0.1", resolvePromise),
  );
  const lifecycleBase = `http://127.0.0.1:${
    (lifecycleServer.address() as AddressInfo).port
  }`;
  try {
    const response = await fetch(
      `${lifecycleBase}/manager/v1/proxies/image/restart`,
      {
        method: "POST",
        headers: { authorization: `Bearer ${adminSecret}` },
      },
    );
    assert.equal(response.status, 202);
    assert.deepEqual(calls, [
      { label: "ai.coreline.heybot.proxy-image", action: "restart" },
    ]);
  } finally {
    await closeServer(lifecycleServer);
  }
});

test("an unavailable proxy degrades only its route while manager health remains live", async () => {
  writeFileSync(
    resolve(root, "config/proxies-unavailable.json"),
    JSON.stringify({
      schemaVersion: 1,
      proxies: [
        {
          id: "image",
          enabled: true,
          exposure: "gateway",
          routePrefix: "/v1/image",
          targetBaseUrl: "http://127.0.0.1:1",
          healthPath: "/health",
          readyPath: "/ready",
          readinessTestPath: "/ready",
          canaryTestPath: "/canary",
          canaryRequiresExplicitConfirmation: true,
          managerClientSecretFile: "./runtime/secrets/image.secret",
          dependencies: [],
        },
      ],
    }),
  );
  const unavailableConfig = loadManagerConfig(
    {
      MANAGER_ROUTE_SECRET_FILE: "./runtime/secrets/route.secret",
      MANAGER_ADMIN_SECRET_FILE: "./runtime/secrets/admin.secret",
      MANAGER_PROXY_REGISTRY_FILE: "./config/proxies-unavailable.json",
      MANAGER_CONNECT_TIMEOUT_MS: "100",
      MANAGER_HEALTH_TIMEOUT_MS: "100",
    },
    root,
  );
  const unavailableServer = createManagerServer(unavailableConfig).server;
  await new Promise<void>((resolvePromise) =>
    unavailableServer.listen(0, "127.0.0.1", resolvePromise),
  );
  const unavailableBase = `http://127.0.0.1:${
    (unavailableServer.address() as AddressInfo).port
  }`;
  try {
    assert.equal((await fetch(`${unavailableBase}/health`)).status, 200);
    assert.equal((await fetch(`${unavailableBase}/ready`)).status, 503);
    const route = await fetch(`${unavailableBase}/v1/image/jobs`, {
      method: "POST",
      headers: {
        authorization: `Bearer ${routeSecret}`,
        "content-type": "application/json",
      },
      body: "{}",
    });
    assert.equal(route.status, 502);
    assert.equal(((await route.json()) as any).error.code, "PROXY_UNAVAILABLE");
    assert.equal((await fetch(`${unavailableBase}/health`)).status, 200);
  } finally {
    await closeServer(unavailableServer);
  }
});
