import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { ProxyRegistry } from "../../src/registry/registry.js";

function registry(proxies: unknown[]): string {
  const root = mkdtempSync(resolve(tmpdir(), "manager-registry-test-"));
  mkdirSync(resolve(root, "config"));
  const path = resolve(root, "config/proxies.json");
  writeFileSync(path, JSON.stringify({ schemaVersion: 1, proxies }));
  return path;
}

function proxy(
  id: string,
  routePrefix: string | undefined,
  targetBaseUrl = "http://127.0.0.1:4347",
): Record<string, unknown> {
  return {
    id,
    enabled: true,
    exposure: routePrefix ? "gateway" : "internal",
    ...(routePrefix ? { routePrefix } : {}),
    targetBaseUrl,
    healthPath: "/health",
    readyPath: "/ready",
    readinessTestPath: "/self-test",
    canaryTestPath: "/canary",
    canaryRequiresExplicitConfirmation: true,
    managerClientSecretFile: `./runtime/secrets/${id}.secret`,
    dependencies: [],
  };
}

test("accepts gateway and internal definitions", () => {
  const loaded = new ProxyRegistry(
    registry([proxy("image", "/v1/image"), proxy("codex", undefined)]),
  );
  assert.equal(loaded.route("/v1/image/jobs")?.id, "image");
  assert.equal(loaded.route("/internal/v1/codex/jobs"), undefined);
});

test("rejects duplicate IDs, prefix conflicts and non-loopback targets", () => {
  assert.throws(() =>
    new ProxyRegistry(registry([proxy("image", "/v1/image"), proxy("image", "/v1/other")])),
  );
  assert.throws(() =>
    new ProxyRegistry(
      registry([proxy("image", "/v1/image"), proxy("nested", "/v1/image/jobs")]),
    ),
  );
  assert.throws(() =>
    new ProxyRegistry(
      registry([proxy("image", "/v1/image", "http://192.168.0.2:4347")]),
    ),
  );
});
