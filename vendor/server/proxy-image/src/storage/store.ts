import { randomUUID } from "node:crypto";
import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { DatabaseSync } from "node:sqlite";
import type { CreateImageJob, ImageJob } from "../jobs/types.js";

interface ImageRow {
  sequence: number;
  id: string;
  request_id: string;
  chat_id: string;
  user_id: string;
  log_id: string;
  prompt: string;
  status: ImageJob["status"];
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  codex_job_id: string | null;
  error_code: string | null;
  artifact_path: string | null;
  artifact_bytes: number | null;
  artifact_sha256: string | null;
}

function map(row: ImageRow): ImageJob {
  return {
    sequence: Number(row.sequence),
    id: row.id,
    requestId: row.request_id,
    chatId: row.chat_id,
    userId: row.user_id,
    logId: row.log_id,
    prompt: row.prompt,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    startedAt: row.started_at ?? undefined,
    finishedAt: row.finished_at ?? undefined,
    codexJobId: row.codex_job_id ?? undefined,
    errorCode: row.error_code ?? undefined,
    artifactPath: row.artifact_path ?? undefined,
    artifactBytes: row.artifact_bytes ?? undefined,
    artifactSha256: row.artifact_sha256 ?? undefined,
  };
}

export class ImageJobStore {
  readonly #db: DatabaseSync;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS jobs (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        id TEXT NOT NULL UNIQUE,
        request_id TEXT NOT NULL UNIQUE,
        chat_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        log_id TEXT NOT NULL,
        prompt TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        codex_job_id TEXT,
        error_code TEXT,
        artifact_path TEXT,
        artifact_bytes INTEGER,
        artifact_sha256 TEXT
      );
      CREATE INDEX IF NOT EXISTS image_queue ON jobs(status, sequence);
      CREATE INDEX IF NOT EXISTS image_room_queue ON jobs(chat_id, status);
    `);
    this.#db.prepare(
      "UPDATE jobs SET status='queued', started_at=NULL, updated_at=? WHERE status='running'",
    ).run(Date.now());
  }

  close(): void {
    this.#db.close();
  }

  countPending(): number {
    const row = this.#db
      .prepare("SELECT count(*) AS count FROM jobs WHERE status IN ('queued','running')")
      .get() as { count: number };
    return Number(row.count);
  }

  countRoomPending(chatId: string): number {
    const row = this.#db
      .prepare(
        "SELECT count(*) AS count FROM jobs WHERE chat_id=? AND status IN ('queued','running')",
      )
      .get(chatId) as { count: number };
    return Number(row.count);
  }

  findByRequest(requestId: string): ImageJob | undefined {
    const row = this.#db.prepare("SELECT * FROM jobs WHERE request_id=?").get(requestId) as
      | ImageRow
      | undefined;
    return row ? map(row) : undefined;
  }

  create(input: CreateImageJob): ImageJob {
    const now = Date.now();
    const id = randomUUID();
    this.#db
      .prepare(
        `INSERT INTO jobs
        (id, request_id, chat_id, user_id, log_id, prompt, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, 'queued', ?, ?)`,
      )
      .run(
        id,
        input.requestId,
        input.chatId,
        input.userId,
        input.logId,
        input.prompt,
        now,
        now,
      );
    return this.get(id)!;
  }

  get(id: string): ImageJob | undefined {
    const row = this.#db.prepare("SELECT * FROM jobs WHERE id=?").get(id) as
      | ImageRow
      | undefined;
    return row ? map(row) : undefined;
  }

  nextQueued(): ImageJob | undefined {
    const row = this.#db
      .prepare("SELECT * FROM jobs WHERE status='queued' ORDER BY sequence LIMIT 1")
      .get() as ImageRow | undefined;
    return row ? map(row) : undefined;
  }

  markRunning(id: string): boolean {
    const now = Date.now();
    const result = this.#db
      .prepare(
        "UPDATE jobs SET status='running', started_at=?, updated_at=? WHERE id=? AND status='queued'",
      )
      .run(now, now, id);
    return Number(result.changes) === 1;
  }

  setCodexJob(id: string, codexJobId: string): void {
    this.#db
      .prepare("UPDATE jobs SET codex_job_id=?, updated_at=? WHERE id=? AND status='running'")
      .run(codexJobId, Date.now(), id);
  }

  succeed(
    id: string,
    artifact: { path: string; bytes: number; sha256: string },
  ): void {
    const now = Date.now();
    this.#db
      .prepare(
        `UPDATE jobs SET status='succeeded', artifact_path=?, artifact_bytes=?,
         artifact_sha256=?, updated_at=?, finished_at=? WHERE id=? AND status='running'`,
      )
      .run(artifact.path, artifact.bytes, artifact.sha256, now, now, id);
  }

  fail(id: string, code: string): void {
    const now = Date.now();
    this.#db
      .prepare(
        `UPDATE jobs SET status='failed', error_code=?, updated_at=?, finished_at=?
         WHERE id=? AND status IN ('queued','running')`,
      )
      .run(code, now, now, id);
  }

  cancel(id: string): boolean {
    const now = Date.now();
    const result = this.#db
      .prepare(
        `UPDATE jobs SET status='cancelled', updated_at=?, finished_at=?
         WHERE id=? AND status IN ('queued','running')`,
      )
      .run(now, now, id);
    return Number(result.changes) === 1;
  }

  expiredArtifacts(cutoff: number): ImageJob[] {
    const rows = this.#db
      .prepare(
        `SELECT * FROM jobs WHERE finished_at < ? AND artifact_path IS NOT NULL`,
      )
      .all(cutoff) as unknown as ImageRow[];
    this.#db
      .prepare(
        `UPDATE jobs SET artifact_path=NULL, artifact_bytes=NULL, artifact_sha256=NULL
         WHERE finished_at < ?`,
      )
      .run(cutoff);
    return rows.map(map);
  }
}
