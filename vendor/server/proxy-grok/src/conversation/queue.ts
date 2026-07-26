type Task<T> = {
  run: () => Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

export class BoundedConversationQueue {
  #active = 0;
  #closed = false;
  readonly #pending: Array<Task<unknown>> = [];

  constructor(
    private readonly maxConcurrency: number,
    private readonly maxPending: number,
  ) {
    if (maxConcurrency < 1 || maxPending < 1) throw new Error("INVALID_TEXT_QUEUE_LIMIT");
  }

  run<T>(task: () => Promise<T>): Promise<T> {
    if (this.#closed) return Promise.reject(new Error("TEXT_QUEUE_CLOSED"));
    if (this.#active + this.#pending.length >= this.maxConcurrency + this.maxPending) {
      return Promise.reject(new Error("TEXT_QUEUE_FULL"));
    }
    return new Promise<T>((resolve, reject) => {
      this.#pending.push({ run: task, resolve: resolve as (value: unknown) => void, reject });
      this.#kick();
    });
  }

  snapshot(): { active: number; pending: number; maxConcurrency: number; maxPending: number } {
    return { active: this.#active, pending: this.#pending.length, maxConcurrency: this.maxConcurrency, maxPending: this.maxPending };
  }

  close(): void {
    this.#closed = true;
    for (const item of this.#pending.splice(0)) item.reject(new Error("TEXT_QUEUE_CLOSED"));
  }

  #kick(): void {
    while (!this.#closed && this.#active < this.maxConcurrency && this.#pending.length > 0) {
      const item = this.#pending.shift()!;
      this.#active += 1;
      void item.run().then(item.resolve, item.reject).finally(() => {
        this.#active -= 1;
        this.#kick();
      });
    }
  }
}
