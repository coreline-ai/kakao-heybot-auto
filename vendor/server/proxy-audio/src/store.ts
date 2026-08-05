import { mkdirSync } from "node:fs";
import { dirname } from "node:path";
import { randomUUID } from "node:crypto";
import { DatabaseSync } from "node:sqlite";
import type { AudioCreateInput, AudioJobStatus, AudioTranscriptResult, StoredAudioJob } from "./types.js";
import { TranscriptCipher } from "./crypto.js";

interface JobRow {
  id: string; request_id: string; chat_id: string; status: AudioJobStatus;
  source_url: string | null; declared_bytes: number; expires_at_ms: number;
  declared_extension: "mp3" | "m4a" | "wav"; language: "ko";
  error_code: string | null; result_ciphertext: string | null;
  created_at_ms: number; updated_at_ms: number;
}

export class AudioJobStore {
  readonly #db: DatabaseSync;
  readonly #cipher: TranscriptCipher;

  constructor(databaseFile: string, transcriptKeyFile: string) {
    mkdirSync(dirname(databaseFile), { recursive: true, mode: 0o700 });
    this.#cipher = new TranscriptCipher(transcriptKeyFile);
    this.#db = new DatabaseSync(databaseFile);
    this.#db.exec("PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA foreign_keys=ON;");
    this.#db.exec(`
      CREATE TABLE IF NOT EXISTS audio_jobs (
        id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, chat_id TEXT NOT NULL,
        status TEXT NOT NULL, source_url TEXT, declared_bytes INTEGER NOT NULL,
        expires_at_ms INTEGER NOT NULL, declared_extension TEXT NOT NULL, language TEXT NOT NULL,
        error_code TEXT, result_ciphertext TEXT, created_at_ms INTEGER NOT NULL, updated_at_ms INTEGER NOT NULL
      );
      CREATE INDEX IF NOT EXISTS audio_jobs_status_idx ON audio_jobs(status, created_at_ms);
      CREATE INDEX IF NOT EXISTS audio_jobs_chat_idx ON audio_jobs(chat_id, created_at_ms);
    `);
    this.#db.prepare(
      "UPDATE audio_jobs SET status='failed', error_code='AUDIO_WORKER_RESTARTED', source_url=NULL, updated_at_ms=? WHERE status IN ('fetching','validating','normalizing','transcribing')"
    ).run(Date.now());
  }

  createOrGet(input: AudioCreateInput): { job: StoredAudioJob; created: boolean } {
    const existing = this.findByRequest(input.requestId);
    if (existing) {
      if (existing.chatId !== input.chatId) throw new Error("REQUEST_SCOPE_MISMATCH");
      return { job: existing, created: false };
    }
    const now = Date.now();
    const id = randomUUID();
    this.#db.prepare(`
      INSERT INTO audio_jobs(id,request_id,chat_id,status,source_url,declared_bytes,expires_at_ms,declared_extension,language,error_code,result_ciphertext,created_at_ms,updated_at_ms)
      VALUES(?,?,?,'queued',?,?,?,?,?,NULL,NULL,?,?)
    `).run(id, input.requestId, input.chatId, input.source.url, input.source.declaredBytes,
      input.source.expiresAtMillis, input.source.declaredExtension, input.language, now, now);
    return { job: this.get(id)!, created: true };
  }

  get(id: string): StoredAudioJob | undefined {
    const row = this.#db.prepare("SELECT * FROM audio_jobs WHERE id=?").get(id) as unknown as JobRow | undefined;
    return row ? this.#decode(row) : undefined;
  }

  findByRequest(requestId: string): StoredAudioJob | undefined {
    const row = this.#db.prepare("SELECT * FROM audio_jobs WHERE request_id=?").get(requestId) as unknown as JobRow | undefined;
    return row ? this.#decode(row) : undefined;
  }

  queued(): StoredAudioJob[] {
    return (this.#db.prepare("SELECT * FROM audio_jobs WHERE status='queued' ORDER BY created_at_ms LIMIT 100").all() as unknown as JobRow[]).map((row) => this.#decode(row));
  }

  countPending(chatId?: string): number {
    const row = chatId
      ? this.#db.prepare("SELECT COUNT(*) AS count FROM audio_jobs WHERE chat_id=? AND status IN ('queued','fetching','validating','normalizing','transcribing')").get(chatId)
      : this.#db.prepare("SELECT COUNT(*) AS count FROM audio_jobs WHERE status IN ('queued','fetching','validating','normalizing','transcribing')").get();
    return Number((row as { count: number }).count);
  }

  claim(id: string): StoredAudioJob | undefined {
    this.#db.exec("BEGIN IMMEDIATE");
    try {
      const row = this.#db.prepare("SELECT * FROM audio_jobs WHERE id=? AND status='queued'").get(id) as unknown as JobRow | undefined;
      if (!row?.source_url) { this.#db.exec("ROLLBACK"); return undefined; }
      this.#db.prepare("UPDATE audio_jobs SET status='fetching', source_url=NULL, updated_at_ms=? WHERE id=? AND status='queued'").run(Date.now(), id);
      this.#db.exec("COMMIT");
      return { ...this.#decode(row), status: "fetching" };
    } catch (error) { this.#db.exec("ROLLBACK"); throw error; }
  }

  setStatus(id: string, status: AudioJobStatus): void {
    this.#db.prepare("UPDATE audio_jobs SET status=?, updated_at_ms=? WHERE id=?").run(status, Date.now(), id);
  }

  succeed(id: string, result: AudioTranscriptResult): void {
    this.#db.prepare("UPDATE audio_jobs SET status='transcribed', result_ciphertext=?, error_code=NULL, source_url=NULL, updated_at_ms=? WHERE id=?")
      .run(this.#cipher.encrypt(result), Date.now(), id);
  }

  fail(id: string, code: string): void {
    this.#db.prepare("UPDATE audio_jobs SET status='failed', error_code=?, source_url=NULL, updated_at_ms=? WHERE id=?")
      .run(code.replace(/[^A-Z0-9_.-]/g, "_").slice(0, 64), Date.now(), id);
  }

  cancel(id: string): boolean {
    const result = this.#db.prepare("UPDATE audio_jobs SET status='cancelled', source_url=NULL, updated_at_ms=? WHERE id=? AND status NOT IN ('transcribed','failed','cancelled')")
      .run(Date.now(), id);
    return Number(result.changes) > 0;
  }

  purge(id: string, chatId: string): boolean {
    const result = this.#db.prepare("DELETE FROM audio_jobs WHERE id=? AND chat_id=?").run(id, chatId);
    return Number(result.changes) > 0;
  }

  cleanup(cutoffMillis: number): number {
    const result = this.#db.prepare("DELETE FROM audio_jobs WHERE updated_at_ms < ? AND status IN ('transcribed','failed','cancelled')").run(cutoffMillis);
    return Number(result.changes);
  }

  close(): void { this.#db.close(); }

  #decode(row: JobRow): StoredAudioJob {
    return {
      id: row.id, requestId: row.request_id, chatId: row.chat_id, status: row.status,
      sourceUrl: row.source_url, declaredBytes: row.declared_bytes, expiresAtMillis: row.expires_at_ms,
      declaredExtension: row.declared_extension, language: row.language, errorCode: row.error_code,
      result: row.result_ciphertext ? this.#cipher.decrypt<AudioTranscriptResult>(row.result_ciphertext) : null,
      createdAtMillis: row.created_at_ms, updatedAtMillis: row.updated_at_ms,
    };
  }
}
