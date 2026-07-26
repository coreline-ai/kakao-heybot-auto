import { randomUUID } from 'node:crypto';
import { mkdirSync } from 'node:fs';
import { dirname } from 'node:path';
import { DatabaseSync } from 'node:sqlite';
import type { GrokJob } from '../jobs/types.js';

type Row = { id:string; request_id:string; status:GrokJob['status']; prompt:string; created_at:number; updated_at:number; started_at:number|null; finished_at:number|null; error_code:string|null; artifact_path:string|null; artifact_bytes:number|null; artifact_sha256:string|null };
const map = (row: Row): GrokJob => ({ id:row.id, requestId:row.request_id, status:row.status, prompt:row.prompt, createdAt:Number(row.created_at), updatedAt:Number(row.updated_at), startedAt:row.started_at ?? undefined, finishedAt:row.finished_at ?? undefined, errorCode:row.error_code ?? undefined, artifactPath:row.artifact_path ?? undefined, artifactBytes:row.artifact_bytes ?? undefined, artifactSha256:row.artifact_sha256 ?? undefined });
export class GrokJobStore {
  readonly #db: DatabaseSync;
  constructor(file: string) { mkdirSync(dirname(file), {recursive:true,mode:0o700}); this.#db=new DatabaseSync(file); this.#db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000; CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, status TEXT NOT NULL, prompt TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL, started_at INTEGER, finished_at INTEGER, error_code TEXT, artifact_path TEXT, artifact_bytes INTEGER, artifact_sha256 TEXT); CREATE INDEX IF NOT EXISTS grok_queue ON jobs(status, created_at);`); this.#db.prepare("UPDATE jobs SET status='queued', started_at=NULL, updated_at=? WHERE status='running'").run(Date.now()); }
  close():void { this.#db.close(); }
  countPending():number { return Number((this.#db.prepare("SELECT count(*) count FROM jobs WHERE status IN ('queued','running')").get() as {count:number}).count); }
  get(id:string):GrokJob|undefined { const row=this.#db.prepare('SELECT * FROM jobs WHERE id=?').get(id) as Row|undefined; return row&&map(row); }
  find(requestId:string):GrokJob|undefined { const row=this.#db.prepare('SELECT * FROM jobs WHERE request_id=?').get(requestId) as Row|undefined; return row&&map(row); }
  create(requestId:string,prompt:string):GrokJob { const now=Date.now(),id=randomUUID(); this.#db.prepare("INSERT INTO jobs (id,request_id,status,prompt,created_at,updated_at) VALUES (?,?,'queued',?,?,?)").run(id,requestId,prompt,now,now); return this.get(id)!; }
  next():GrokJob|undefined { const row=this.#db.prepare("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").get() as Row|undefined; return row&&map(row); }
  running(id:string):boolean { return Number(this.#db.prepare("UPDATE jobs SET status='running',started_at=?,updated_at=? WHERE id=? AND status='queued'").run(Date.now(),Date.now(),id).changes)===1; }
  cancel(id:string):boolean { return Number(this.#db.prepare("UPDATE jobs SET status='cancelled',finished_at=?,updated_at=? WHERE id=? AND status IN ('queued','running')").run(Date.now(),Date.now(),id).changes)===1; }
  succeed(id:string,a:{path:string;bytes:number;sha256:string}):void { const n=Date.now(); this.#db.prepare("UPDATE jobs SET status='succeeded',artifact_path=?,artifact_bytes=?,artifact_sha256=?,finished_at=?,updated_at=? WHERE id=? AND status='running'").run(a.path,a.bytes,a.sha256,n,n,id); }
  fail(id:string,code:string):void { const n=Date.now(); this.#db.prepare("UPDATE jobs SET status='failed',error_code=?,finished_at=?,updated_at=? WHERE id=? AND status IN ('queued','running')").run(code,n,n,id); }
}
