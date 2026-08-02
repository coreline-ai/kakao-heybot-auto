import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { CreateVisionJob, VisionJob, VisionResult, VisionStatus } from "./types.js";

interface Row {
  sequence: number; id: string; request_id: string; chat_id: string; user_id: string; log_id: string;
  task: VisionJob["task"];
  source_url: string | null; source_width: number; source_height: number; source_bytes: number; source_expires: number;
  status: VisionStatus; created_at: number; updated_at: number; error_code: string | null; result_json: string | null;
}

function map(row: Row): VisionJob {
  return {
    id: row.id, sequence: Number(row.sequence), requestId: row.request_id, chatId: row.chat_id,
    userId: row.user_id, logId: row.log_id, task: row.task,
    source: {
      // Cleared sources are never returned to public callers and only occur for terminal jobs.
      url: row.source_url ?? "", width: row.source_width, height: row.source_height,
      declaredBytes: row.source_bytes, expiresAtMillis: row.source_expires,
    },
    status: row.status, createdAt: row.created_at, updatedAt: row.updated_at,
    errorCode: row.error_code ?? undefined,
    result: row.result_json ? JSON.parse(row.result_json) as VisionResult : undefined,
  };
}

export class VisionStore {
  readonly #db: DatabaseSync;
  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
      CREATE TABLE IF NOT EXISTS jobs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
        request_id TEXT UNIQUE NOT NULL, chat_id TEXT NOT NULL, user_id TEXT NOT NULL, log_id TEXT NOT NULL,
        task TEXT NOT NULL DEFAULT 'describe',
        source_url TEXT, source_width INTEGER NOT NULL, source_height INTEGER NOT NULL,
        source_bytes INTEGER NOT NULL, source_expires INTEGER NOT NULL,
        status TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
        error_code TEXT, result_json TEXT
      );
      CREATE INDEX IF NOT EXISTS vision_queue ON jobs(status, sequence);
      CREATE INDEX IF NOT EXISTS vision_room ON jobs(chat_id, status);
    `);
    const columns = this.#db.prepare("PRAGMA table_info(jobs)").all() as unknown as Array<{name:string}>;
    if (!columns.some((column) => column.name === "task")) {
      this.#db.exec("ALTER TABLE jobs ADD COLUMN task TEXT NOT NULL DEFAULT 'describe'");
    }
    this.#db.prepare("UPDATE jobs SET status='queued', updated_at=? WHERE status='running'").run(Date.now());
  }
  close(): void { this.#db.close(); }
  get(id: string): VisionJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE id=?").get(id) as Row | undefined; return row ? map(row) : undefined; }
  byRequest(id: string): VisionJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE request_id=?").get(id) as Row | undefined; return row ? map(row) : undefined; }
  countPending(): number { return Number((this.#db.prepare("SELECT count(*) count FROM jobs WHERE status IN ('queued','running')").get() as {count:number}).count); }
  countRoomPending(chatId: string): number { return Number((this.#db.prepare("SELECT count(*) count FROM jobs WHERE chat_id=? AND status IN ('queued','running')").get(chatId) as {count:number}).count); }
  next(): VisionJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1").get() as Row | undefined; return row ? map(row) : undefined; }
  create(input: CreateVisionJob): VisionJob {
    const id = randomUUID(); const now = Date.now();
    this.#db.prepare(`INSERT INTO jobs
      (id,request_id,chat_id,user_id,log_id,task,source_url,source_width,source_height,source_bytes,source_expires,status,created_at,updated_at)
      VALUES(?,?,?,?,?,?,?,?,?,?,?,'queued',?,?)`).run(
        id,input.requestId,input.chatId,input.userId,input.logId,input.task,input.source.url,input.source.width,input.source.height,
        input.source.declaredBytes,input.source.expiresAtMillis,now,now,
      );
    return this.get(id)!;
  }
  markRunning(id: string): boolean { return Number(this.#db.prepare("UPDATE jobs SET status='running',updated_at=? WHERE id=? AND status='queued'").run(Date.now(),id).changes) === 1; }
  succeed(id: string, result: VisionResult): void { const now=Date.now(); this.#db.prepare("UPDATE jobs SET status='succeeded',result_json=?,source_url=NULL,updated_at=? WHERE id=? AND status='running'").run(JSON.stringify(result),now,id); }
  fail(id: string, code: string): void { this.#db.prepare("UPDATE jobs SET status='failed',error_code=?,source_url=NULL,updated_at=? WHERE id=? AND status IN ('queued','running')").run(code,Date.now(),id); }
  cancel(id: string): boolean { return Number(this.#db.prepare("UPDATE jobs SET status='cancelled',source_url=NULL,updated_at=? WHERE id=? AND status IN ('queued','running')").run(Date.now(),id).changes) === 1; }
}
