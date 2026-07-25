import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import type { CodexJob, CreateCodexJob } from "./types.js";

interface JobRow {
  id: string;
  caller: string;
  request_id: string;
  capability: string;
  prompt: string;
  status: CodexJob["status"];
  created_at: number;
  updated_at: number;
  started_at: number | null;
  finished_at: number | null;
  error_code: string | null;
  artifact_id: string | null;
  artifact_path: string | null;
  artifact_bytes: number | null;
  artifact_sha256: string | null;
}

function map(row: JobRow): CodexJob {
  return {
    id: row.id,
    caller: row.caller,
    requestId: row.request_id,
    capability: row.capability,
    prompt: row.prompt,
    status: row.status,
    createdAt: row.created_at,
    updatedAt: row.updated_at,
    startedAt: row.started_at ?? undefined,
    finishedAt: row.finished_at ?? undefined,
    errorCode: row.error_code ?? undefined,
    artifactId: row.artifact_id ?? undefined,
    artifactPath: row.artifact_path ?? undefined,
    artifactBytes: row.artifact_bytes ?? undefined,
    artifactSha256: row.artifact_sha256 ?? undefined,
  };
}

export class CodexJobStore {
  readonly #db: DatabaseSync;

  constructor(path: string) {
    mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
    this.#db = new DatabaseSync(path);
    this.#db.exec(`
      PRAGMA journal_mode = WAL;
      PRAGMA busy_timeout = 5000;
      CREATE TABLE IF NOT EXISTS jobs (
        id TEXT PRIMARY KEY,
        caller TEXT NOT NULL,
        request_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        prompt TEXT NOT NULL,
        status TEXT NOT NULL,
        created_at INTEGER NOT NULL,
        updated_at INTEGER NOT NULL,
        started_at INTEGER,
        finished_at INTEGER,
        error_code TEXT,
        artifact_id TEXT,
        artifact_path TEXT,
        artifact_bytes INTEGER,
        artifact_sha256 TEXT,
        UNIQUE(caller, request_id)
      );
      CREATE INDEX IF NOT EXISTS jobs_queue ON jobs(status, created_at);
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

  createOrGet(input: CreateCodexJob): { job: CodexJob; created: boolean } {
    const existing = this.findByRequest(input.caller, input.requestId);
    if (existing) return { job: existing, created: false };
    const now = Date.now();
    const id = randomUUID();
    this.#db
      .prepare(
        `INSERT INTO jobs
         (id, caller, request_id, capability, prompt, status, created_at, updated_at)
         VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)`,
      )
      .run(id, input.caller, input.requestId, input.capability, input.prompt, now, now);
    return { job: this.get(id)!, created: true };
  }

  findByRequest(caller: string, requestId: string): CodexJob | undefined {
    const row = this.#db
      .prepare("SELECT * FROM jobs WHERE caller=? AND request_id=?")
      .get(caller, requestId) as JobRow | undefined;
    return row ? map(row) : undefined;
  }

  get(id: string): CodexJob | undefined {
    const row = this.#db.prepare("SELECT * FROM jobs WHERE id=?").get(id) as
      | JobRow
      | undefined;
    return row ? map(row) : undefined;
  }

  nextQueued(): CodexJob | undefined {
    const row = this.#db
      .prepare("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at, rowid LIMIT 1")
      .get() as JobRow | undefined;
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

  succeed(
    id: string,
    artifact: { id: string; path: string; bytes: number; sha256: string },
  ): void {
    const now = Date.now();
    this.#db
      .prepare(
        `UPDATE jobs SET status='succeeded', updated_at=?, finished_at=?,
         artifact_id=?, artifact_path=?, artifact_bytes=?, artifact_sha256=?
         WHERE id=? AND status='running'`,
      )
      .run(
        now,
        now,
        artifact.id,
        artifact.path,
        artifact.bytes,
        artifact.sha256,
        id,
      );
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

  deleteExpiredArtifacts(cutoff: number): CodexJob[] {
    const rows = this.#db
      .prepare(
        `SELECT * FROM jobs WHERE status IN ('succeeded','failed','cancelled')
         AND finished_at < ? AND artifact_path IS NOT NULL`,
      )
      .all(cutoff) as unknown as JobRow[];
    this.#db
      .prepare(
        `UPDATE jobs SET artifact_id=NULL, artifact_path=NULL, artifact_bytes=NULL,
         artifact_sha256=NULL WHERE finished_at < ?`,
      )
      .run(cutoff);
    return rows.map(map);
  }
}
