import type { AudioProxyConfig } from "./config.js";
import type { AudioPipeline } from "./pipeline.js";
import { AudioJobStore } from "./store.js";
import type { AudioCreateInput, PublicAudioJob, StoredAudioJob } from "./types.js";
import { toPublicJob } from "./types.js";

export interface AudioProcessorPort {
  create(input: AudioCreateInput): { job: PublicAudioJob; created: boolean };
  get(id: string, chatId: string): PublicAudioJob | undefined;
  cancel(id: string, chatId: string): PublicAudioJob | undefined;
  purge(id: string, chatId: string): boolean;
  readiness(): Promise<{ ready: boolean; reason?: string; version?: string }>;
  close(): Promise<void>;
}

export class AudioJobProcessor implements AudioProcessorPort {
  readonly #active = new Map<string, { chatId: string; abort: AbortController }>();
  #closed = false;

  constructor(
    private readonly store: AudioJobStore,
    private readonly pipeline: AudioPipeline,
    private readonly config: AudioProxyConfig,
  ) {}

  create(input: AudioCreateInput): { job: PublicAudioJob; created: boolean } {
    const existing = this.store.findByRequest(input.requestId);
    if (!existing && this.store.countPending(input.chatId) >= this.config.maxPendingPerRoom) {
      throw new Error("ROOM_QUEUE_LIMIT");
    }
    const result = this.store.createOrGet(input);
    this.kick();
    return { job: toPublicJob(result.job), created: result.created };
  }

  get(id: string, chatId: string): PublicAudioJob | undefined {
    const job = this.store.get(id);
    return job?.chatId === chatId ? toPublicJob(job) : undefined;
  }

  cancel(id: string, chatId: string): PublicAudioJob | undefined {
    const job = this.store.get(id);
    if (!job || job.chatId !== chatId) return undefined;
    this.#active.get(id)?.abort.abort();
    this.store.cancel(id);
    return toPublicJob(this.store.get(id)!);
  }

  purge(id: string, chatId: string): boolean {
    this.#active.get(id)?.abort.abort();
    return this.store.purge(id, chatId);
  }

  readiness() { return this.pipeline.readiness(); }

  kick(): void {
    if (this.#closed) return;
    queueMicrotask(() => this.#drain());
  }

  async close(): Promise<void> {
    this.#closed = true;
    for (const value of this.#active.values()) value.abort.abort();
    while (this.#active.size) await new Promise((resolve) => setTimeout(resolve, 10));
    this.store.close();
  }

  #drain(): void {
    if (this.#closed || this.#active.size >= this.config.maxConcurrency) return;
    const activeRooms = new Set([...this.#active.values()].map((item) => item.chatId));
    const next = this.store.queued().find((job) => !activeRooms.has(job.chatId));
    if (!next) return;
    const claimed = this.store.claim(next.id);
    if (!claimed) return;
    const abort = new AbortController();
    this.#active.set(claimed.id, { chatId: claimed.chatId, abort });
    void this.#run(claimed, abort).finally(() => {
      this.#active.delete(claimed.id);
      this.#drain();
    });
    this.#drain();
  }

  async #run(job: StoredAudioJob, controller: AbortController): Promise<void> {
    try {
      const result = await this.pipeline.run(job, (status) => this.store.setStatus(job.id, status), controller.signal);
      if (controller.signal.aborted) { this.store.cancel(job.id); return; }
      this.store.succeed(job.id, result);
    } catch (error) {
      if (controller.signal.aborted) this.store.cancel(job.id);
      else this.store.fail(job.id, (error as Error).message || "AUDIO_PROCESS_FAILED");
    }
  }
}
