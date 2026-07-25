import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import type { CodexRunner, RawArtifact } from "../../src/cli/runner.js";
import { loadCodexProxyConfig } from "../../src/config/config.js";
import type { CodexJob } from "../../src/jobs/types.js";
import { CodexJobStore } from "../../src/jobs/store.js";
import { CodexJobProcessor } from "../../src/queue/processor.js";

class GateRunner implements CodexRunner {
  readonly started: string[] = [];
  readonly gates = new Map<
    string,
    { resolve: (artifact: RawArtifact) => void; reject: (error: Error) => void }
  >();

  async readiness(): Promise<{ ready: boolean }> {
    return { ready: true };
  }

  run(job: CodexJob, signal: AbortSignal): Promise<RawArtifact> {
    this.started.push(job.requestId);
    return new Promise<RawArtifact>((resolvePromise, reject) => {
      this.gates.set(job.id, { resolve: resolvePromise, reject });
      signal.addEventListener("abort", () => reject(new Error("JOB_CANCELLED")), {
        once: true,
      });
    });
  }

  finish(job: CodexJob, root: string): void {
    const path = resolve(root, `${job.id}.png`);
    writeFileSync(path, "artifact");
    this.gates.get(job.id)?.resolve({
      id: `${job.id}-artifact`,
      path,
      bytes: 8,
      sha256: "a".repeat(64),
    });
  }
}

async function waitFor(predicate: () => boolean, timeoutMs = 2_000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolvePromise) => setTimeout(resolvePromise, 10));
  }
  throw new Error("condition timeout");
}

test("runs global FIFO at configured concurrency and cancellation frees the worker", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "codex-processor-test-"));
  const config = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_PROXY_RUNNER: "fake",
      CODEX_PROXY_QUEUE_CONCURRENCY: "1",
    },
    root,
  );
  const store = new CodexJobStore(config.databaseFile);
  const runner = new GateRunner();
  const processor = new CodexJobProcessor(store, runner, config);
  const jobs = ["first", "second", "third"].map(
    (requestId) =>
      store.createOrGet({
        caller: "image",
        requestId,
        capability: "image.generate",
        prompt: requestId,
      }).job,
  );

  processor.start();
  await waitFor(() => runner.started.length === 1);
  assert.deepEqual(runner.started, ["first"]);

  runner.finish(jobs[0]!, root);
  await waitFor(() => runner.started.length === 2);
  assert.deepEqual(runner.started, ["first", "second"]);

  assert.equal(processor.cancel(jobs[1]!.id), true);
  await waitFor(() => runner.started.length === 3);
  assert.deepEqual(runner.started, ["first", "second", "third"]);
  assert.equal(store.get(jobs[1]!.id)?.status, "cancelled");

  runner.finish(jobs[2]!, root);
  await waitFor(() => store.get(jobs[2]!.id)?.status === "succeeded");
  assert.equal(processor.snapshot().maxConcurrency, 1);
  await processor.close();
  store.close();
});

test("fails queued work that exceeds the queue wait timeout", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "codex-queue-timeout-test-"));
  const base = loadCodexProxyConfig(
    {
      CODEX_PROXY_RUNTIME_DIR: "./runtime",
      CODEX_PROXY_RUNNER: "fake",
      CODEX_PROXY_QUEUE_CONCURRENCY: "1",
    },
    root,
  );
  const config = { ...base, queueWaitTimeoutMs: 20 };
  const store = new CodexJobStore(config.databaseFile);
  const runner = new GateRunner();
  const processor = new CodexJobProcessor(store, runner, config);
  const first = store.createOrGet({
    caller: "image",
    requestId: "running",
    capability: "image.generate",
    prompt: "running",
  }).job;
  const expired = store.createOrGet({
    caller: "image",
    requestId: "expired",
    capability: "image.generate",
    prompt: "expired",
  }).job;

  processor.start();
  await waitFor(() => runner.started.length === 1);
  await new Promise((resolvePromise) => setTimeout(resolvePromise, 30));
  runner.finish(first, root);
  await waitFor(() => store.get(expired.id)?.status === "failed");
  assert.equal(store.get(expired.id)?.errorCode, "QUEUE_WAIT_TIMEOUT");
  assert.deepEqual(runner.started, ["running"]);

  await processor.close();
  store.close();
});
