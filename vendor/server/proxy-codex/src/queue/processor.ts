import { unlinkSync } from "node:fs";
import type { CodexProxyConfig } from "../config/config.js";
import type { CodexRunner } from "../cli/runner.js";
import { CodexJobStore } from "../jobs/store.js";

export class CodexJobProcessor {
  readonly #controllers = new Map<string, AbortController>();
  #active = 0;
  #closed = false;
  #cleanupTimer?: NodeJS.Timeout;
  readonly #tasks = new Set<Promise<void>>();
  #closePromise?: Promise<void>;

  constructor(
    readonly store: CodexJobStore,
    private readonly runner: CodexRunner,
    private readonly config: CodexProxyConfig,
  ) {}

  start(): void {
    this.#cleanupTimer = setInterval(() => this.cleanup(), 60_000);
    this.#cleanupTimer.unref();
    this.kick();
  }

  close(): Promise<void> {
    if (this.#closePromise) return this.#closePromise;
    this.#closed = true;
    if (this.#cleanupTimer) clearInterval(this.#cleanupTimer);
    for (const controller of this.#controllers.values()) controller.abort();
    this.#closePromise = Promise.allSettled([...this.#tasks]).then(() => undefined);
    return this.#closePromise;
  }

  snapshot(): { active: number; maxConcurrency: number; pending: number } {
    return {
      active: this.#active,
      maxConcurrency: this.config.queueConcurrency,
      pending: this.store.countPending(),
    };
  }

  cancel(jobId: string): boolean {
    const changed = this.store.cancel(jobId);
    this.#controllers.get(jobId)?.abort();
    return changed;
  }

  kick(): void {
    if (this.#closed) return;
    while (this.#active < this.config.queueConcurrency) {
      const job = this.store.nextQueued();
      if (!job) return;
      if (Date.now() - job.createdAt > this.config.queueWaitTimeoutMs) {
        this.store.fail(job.id, "QUEUE_WAIT_TIMEOUT");
        continue;
      }
      if (!this.store.markRunning(job.id)) continue;
      this.#active += 1;
      const controller = new AbortController();
      this.#controllers.set(job.id, controller);
      const task = this.runner
        .run({ ...job, status: "running" }, controller.signal)
        .then((artifact) => {
          const current = this.store.get(job.id);
          if (current?.status === "cancelled") {
            try {
              unlinkSync(artifact.path);
            } catch {
              // Artifact may already have been removed by cleanup.
            }
            return;
          }
          this.store.succeed(job.id, artifact);
        })
        .catch((error: Error) => {
          const current = this.store.get(job.id);
          if (current?.status !== "cancelled") {
            this.store.fail(job.id, error.message.replace(/[^A-Z0-9_]/g, "_").slice(0, 64));
          }
        })
        .finally(() => {
          this.#controllers.delete(job.id);
          this.#active -= 1;
          setImmediate(() => this.kick());
        });
      this.#tasks.add(task);
      void task.finally(() => this.#tasks.delete(task));
    }
  }

  private cleanup(): void {
    const cutoff =
      Date.now() - this.config.artifactRetentionHours * 60 * 60 * 1_000;
    for (const job of this.store.deleteExpiredArtifacts(cutoff)) {
      if (!job.artifactPath) continue;
      try {
        unlinkSync(job.artifactPath);
      } catch {
        // Missing artifacts are already effectively cleaned.
      }
    }
  }
}
