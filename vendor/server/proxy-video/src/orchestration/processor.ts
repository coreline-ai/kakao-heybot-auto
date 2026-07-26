import { chmodSync, mkdirSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { VideoProxyConfig } from "../config/config.js";
import { GrokClient } from "../clients/grok/client.js";
import { validateMp4 } from "../videos/qc.js";
import { VideoJobStore } from "../storage/store.js";

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolvePromise, reject) => {
    const timer = setTimeout(resolvePromise, milliseconds);
    const abort = (): void => {
      clearTimeout(timer);
      reject(new Error("JOB_CANCELLED"));
    };
    signal.addEventListener("abort", abort, { once: true });
  });
}

export class VideoJobProcessor {
  readonly #controllers = new Map<string, AbortController>();
  #active = 0;
  #closed = false;
  #cleanupTimer?: NodeJS.Timeout;
  readonly #tasks = new Set<Promise<void>>();
  #closePromise?: Promise<void>;

  constructor(
    readonly store: VideoJobStore,
    private readonly grok: GrokClient,
    private readonly config: VideoProxyConfig,
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

  snapshot(): {
    active: number;
    maxConcurrency: number;
    pending: number;
  } {
    return {
      active: this.#active,
      maxConcurrency: this.config.queueConcurrency,
      pending: this.store.countPending(),
    };
  }

  async cancel(jobId: string): Promise<boolean> {
    const job = this.store.get(jobId);
    if (!job) return false;
    const changed = this.store.cancel(jobId);
    this.#controllers.get(jobId)?.abort();
    if (job.grokJobId) await this.grok.cancel(job.grokJobId);
    return changed;
  }

  kick(): void {
    if (this.#closed) return;
    while (this.#active < this.config.queueConcurrency) {
      const job = this.store.nextQueued();
      if (!job) return;
      if (Date.now() - job.createdAt > this.config.queueWaitTimeoutMs) {
        this.store.fail(job.id, "VIDEO_QUEUE_WAIT_TIMEOUT");
        continue;
      }
      if (!this.store.markRunning(job.id)) continue;
      this.#active += 1;
      const controller = new AbortController();
      this.#controllers.set(job.id, controller);
      const task = this.run(job.id, controller.signal)
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

  private async run(jobId: string, signal: AbortSignal): Promise<void> {
    const job = this.store.get(jobId);
    if (!job) throw new Error("JOB_NOT_FOUND");
    const created = await this.grok.create(job.id, job.prompt, signal);
    this.store.setGrokJob(job.id, created.jobId);
    const deadline = Date.now() + this.config.grokJobTimeoutMs;
    let status = created;
    while (status.status === "queued" || status.status === "running") {
      if (Date.now() >= deadline) {
        await this.grok.cancel(created.jobId);
        throw new Error("GROK_JOB_TIMEOUT");
      }
      await delay(500, signal);
      status = await this.grok.get(created.jobId, signal);
    }
    if (status.status !== "succeeded") {
      throw new Error(status.error?.code || `GROK_${status.status.toUpperCase()}`);
    }
    const artifact = status.artifacts[0];
    if (!artifact || artifact.mediaType !== "video/mp4" || status.artifacts.length !== 1) {
      throw new Error("GROK_ARTIFACT_CONTRACT");
    }
    const data = await this.grok.download(created.jobId, artifact.artifactId, signal);
    const finalPath = resolve(this.config.runtimeDir, "artifacts", `${job.id}.mp4`);
    const temporaryPath = `${finalPath}.tmp`;
    mkdirSync(dirname(finalPath), { recursive: true, mode: 0o700 });
    writeFileSync(temporaryPath, data, { mode: 0o600 });
    const qc = await validateMp4(temporaryPath, data, this.config.videoMaxBytes, this.config.ffprobeCommand);
    if (artifact.sha256 && artifact.sha256 !== qc.sha256) {
      throw new Error("GROK_ARTIFACT_HASH_MISMATCH");
    }
    renameSync(temporaryPath, finalPath);
    chmodSync(finalPath, 0o600);
    this.store.succeed(job.id, {
      path: finalPath,
      bytes: qc.bytes,
      sha256: qc.sha256,
    });
  }

  private cleanup(): void {
    const cutoff =
      Date.now() - this.config.artifactRetentionHours * 60 * 60 * 1_000;
    for (const job of this.store.expiredArtifacts(cutoff)) {
      if (!job.artifactPath) continue;
      try {
        unlinkSync(job.artifactPath);
      } catch {
        // Missing artifact is already cleaned.
      }
    }
  }
}
