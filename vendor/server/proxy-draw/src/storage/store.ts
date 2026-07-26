import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { CreateDrawJob, DrawJob } from "../jobs/types.js";

type Row = {
  sequence: number; id: string; request_id: string; chat_id: string; user_id: string; log_id: string; prompt: string;
  status: DrawJob["status"]; created_at: number; updated_at: number; started_at: number | null; finished_at: number | null;
  codex_job_id: string | null; brush_job_id: string | null; error_code: string | null;
  artifact_path: string | null; artifact_bytes: number | null; artifact_sha256: string | null;
};

function map(row: Row): DrawJob {
  return { sequence: Number(row.sequence), id: row.id, requestId: row.request_id, chatId: row.chat_id, userId: row.user_id, logId: row.log_id, prompt: row.prompt, status: row.status,
    createdAt: Number(row.created_at), updatedAt: Number(row.updated_at), startedAt: row.started_at ?? undefined, finishedAt: row.finished_at ?? undefined,
    codexJobId: row.codex_job_id ?? undefined, brushJobId: row.brush_job_id ?? undefined, errorCode: row.error_code ?? undefined,
    artifactPath: row.artifact_path ?? undefined, artifactBytes: row.artifact_bytes ?? undefined, artifactSha256: row.artifact_sha256 ?? undefined };
}

export class DrawJobStore {
  readonly #db: DatabaseSync;
  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 }); this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode = WAL; PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS jobs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT NOT NULL UNIQUE, request_id TEXT NOT NULL UNIQUE,
        chat_id TEXT NOT NULL, user_id TEXT NOT NULL, log_id TEXT NOT NULL, prompt TEXT NOT NULL, status TEXT NOT NULL,
        created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER,
        codex_job_id TEXT, brush_job_id TEXT, error_code TEXT, artifact_path TEXT, artifact_bytes INTEGER, artifact_sha256 TEXT
      );
      CREATE INDEX IF NOT EXISTS draw_queue ON jobs(status, sequence);
      CREATE INDEX IF NOT EXISTS draw_room_queue ON jobs(chat_id, status);
    `);
    this.#db.prepare("UPDATE jobs SET status='queued', started_at=NULL, updated_at=? WHERE status='running'").run(Date.now());
  }
  close(): void { this.#db.close(); }
  get(id: string): DrawJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE id=?").get(id) as Row | undefined; return row ? map(row) : undefined; }
  findByRequest(requestId: string): DrawJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE request_id=?").get(requestId) as Row | undefined; return row ? map(row) : undefined; }
  create(input: CreateDrawJob): DrawJob { const now = Date.now(); const id = randomUUID(); this.#db.prepare("INSERT INTO jobs (id,request_id,chat_id,user_id,log_id,prompt,status,created_at,updated_at) VALUES (?,?,?,?,?,?,'queued',?,?)").run(id,input.requestId,input.chatId,input.userId,input.logId,input.prompt,now,now); return this.get(id)!; }
  countPending(): number { return Number((this.#db.prepare("SELECT count(*) AS count FROM jobs WHERE status IN ('queued','running')").get() as {count:number}).count); }
  countRoomPending(chatId: string): number { return Number((this.#db.prepare("SELECT count(*) AS count FROM jobs WHERE chat_id=? AND status IN ('queued','running')").get(chatId) as {count:number}).count); }
  nextQueued(): DrawJob | undefined { const row = this.#db.prepare("SELECT * FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1").get() as Row | undefined; return row ? map(row) : undefined; }
  markRunning(id: string): boolean { const now=Date.now(); return Number(this.#db.prepare("UPDATE jobs SET status='running',started_at=?,updated_at=? WHERE id=? AND status='queued'").run(now,now,id).changes)===1; }
  setCodexJob(id: string, codexJobId: string): void { this.#db.prepare("UPDATE jobs SET codex_job_id=?,updated_at=? WHERE id=? AND status='running'").run(codexJobId,Date.now(),id); }
  setBrushJob(id: string, brushJobId: string): void { this.#db.prepare("UPDATE jobs SET brush_job_id=?,updated_at=? WHERE id=? AND status='running'").run(brushJobId,Date.now(),id); }
  succeed(id: string, artifact: {path:string;bytes:number;sha256:string}): void { const now=Date.now(); this.#db.prepare("UPDATE jobs SET status='succeeded',artifact_path=?,artifact_bytes=?,artifact_sha256=?,updated_at=?,finished_at=? WHERE id=? AND status='running'").run(artifact.path,artifact.bytes,artifact.sha256,now,now,id); }
  fail(id: string, code: string): void { const now=Date.now(); this.#db.prepare("UPDATE jobs SET status='failed',error_code=?,updated_at=?,finished_at=? WHERE id=? AND status IN ('queued','running')").run(code,now,now,id); }
  cancel(id: string): boolean { const now=Date.now(); return Number(this.#db.prepare("UPDATE jobs SET status='cancelled',updated_at=?,finished_at=? WHERE id=? AND status IN ('queued','running')").run(now,now,id).changes)===1; }
  expiredArtifacts(cutoff: number): DrawJob[] { const rows=this.#db.prepare("SELECT * FROM jobs WHERE finished_at < ? AND artifact_path IS NOT NULL").all(cutoff) as unknown as Row[]; this.#db.prepare("UPDATE jobs SET artifact_path=NULL,artifact_bytes=NULL,artifact_sha256=NULL WHERE finished_at < ?").run(cutoff); return rows.map(map); }
}
