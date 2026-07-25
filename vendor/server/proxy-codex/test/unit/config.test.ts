import assert from "node:assert/strict";
import { test } from "node:test";
import { loadCodexProxyConfig } from "../../src/config/config.js";

test("requires loopback and validated integer settings", () => {
  assert.throws(() =>
    loadCodexProxyConfig({ CODEX_PROXY_HOST: "0.0.0.0" }, "/tmp"),
  );
  assert.throws(() =>
    loadCodexProxyConfig({ CODEX_PROXY_QUEUE_CONCURRENCY: "many" }, "/tmp"),
  );
  assert.equal(
    loadCodexProxyConfig({ CODEX_PROXY_RUNNER: "fake" }, "/tmp").queueConcurrency,
    1,
  );
});
