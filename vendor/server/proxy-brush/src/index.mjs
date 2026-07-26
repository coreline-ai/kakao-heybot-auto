import { createHash, randomUUID, timingSafeEqual } from "node:crypto";
import { spawn } from "node:child_process";
import { chmodSync, createReadStream, existsSync, lstatSync, mkdirSync, readFileSync, renameSync, statSync, unlinkSync, writeFileSync } from "node:fs";
import { chmod, mkdir, readFile, rm, writeFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const MODULE_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const PNG_SIGNATURE = Buffer.from("89504e470d0a1a0a", "hex");
const JOB_ID = /^[0-9a-f-]{36}$/;
const REQUEST_ID = /^[A-Za-z0-9._:-]{1,128}$/;
const MAX_BODY = 16 * 1024 * 1024;
const MAX_SOURCE = 10 * 1024 * 1024;
const MAX_PIXELS = 33_177_600;
const MAX_ARTIFACT = 32 * 1024 * 1024;
const MAX_LOG = 1024 * 1024;

function integer(raw, fallback, minimum, maximum, name) {
  if (raw === undefined) return fallback;
  if (!/^\d+$/.test(raw.trim())) throw new Error(`${name} must be an integer`);
  const value = Number(raw);
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) throw new Error(`${name} out of range`);
  return value;
}

function absolute(env, name, fallback, root) {
  return resolve(root, env[name]?.trim() || fallback);
}

export function loadConfig(env = process.env, root = MODULE_ROOT) {
  const host = env.PEN_BRUSH_PROXY_HOST?.trim() || "127.0.0.1";
  if (!["127.0.0.1", "::1", "localhost"].includes(host)) throw new Error("PEN_BRUSH_PROXY_HOST must be loopback");
  const runtimeDir = absolute(env, "PEN_BRUSH_PROXY_RUNTIME_DIR", "./runtime", root);
  const browserDir = absolute(env, "PEN_BRUSH_PROXY_BROWSER_DIR", "./runtime/remotion-browser", root);
  const engineRoot = absolute(env, "PEN_BRUSH_PROXY_ENGINE_ROOT", "./engine", root);
  const pythonBin = absolute(env, "PEN_BRUSH_PROXY_PYTHON", "./runtime/python-venv/bin/python", root);
  const secretFile = absolute(env, "PEN_BRUSH_PROXY_DRAW_SECRET_FILE", "./runtime/secrets/draw-upstream.secret", root);
  return {
    root,
    host,
    port: integer(env.PEN_BRUSH_PROXY_PORT, 4360, 1, 65535, "PEN_BRUSH_PROXY_PORT"),
    enabled: (env.PEN_BRUSH_PROXY_ENABLED ?? "false").toLowerCase() === "true",
    runtimeDir,
    browserDir,
    engineRoot,
    pythonBin,
    secretFile,
    serviceId: env.PEN_BRUSH_PROXY_DRAW_SERVICE_ID?.trim() || "draw",
    timeoutMs: integer(env.PEN_BRUSH_PROXY_TIMEOUT_MS, 900_000, 60_000, 3_600_000, "PEN_BRUSH_PROXY_TIMEOUT_MS"),
    maxPending: integer(env.PEN_BRUSH_PROXY_MAX_PENDING, 1, 1, 4, "PEN_BRUSH_PROXY_MAX_PENDING"),
    maxArtifactBytes: integer(env.PEN_BRUSH_PROXY_MAX_ARTIFACT_BYTES, MAX_ARTIFACT, 1_024_000, 200 * 1024 * 1024, "PEN_BRUSH_PROXY_MAX_ARTIFACT_BYTES"),
  };
}

function json(response, status, body) {
  const data = Buffer.from(JSON.stringify(body));
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": data.length,
    "cache-control": "no-store",
    "x-content-type-options": "nosniff",
  });
  response.end(data);
}

function readSecret(path) {
  const value = readFileSync(path, "utf8").trim();
  if (!value || value.length > 512) throw new Error("PEN_BRUSH_SECRET_INVALID");
  return value;
}

function safeEquals(actual, expected) {
  const a = Buffer.from(actual || "");
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function authenticate(request, config) {
  const authorization = request.headers.authorization;
  const bearer = typeof authorization === "string" && authorization.startsWith("Bearer ")
    ? authorization.slice("Bearer ".length) : "";
  return request.headers["x-heybot-service-id"] === config.serviceId && safeEquals(bearer, readSecret(config.secretFile));
}

async function readJson(request) {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of request) {
    const buffer = Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > MAX_BODY) throw new Error("BODY_TOO_LARGE");
    chunks.push(buffer);
  }
  try { return JSON.parse(Buffer.concat(chunks).toString("utf8")); }
  catch { throw new Error("INVALID_JSON"); }
}

function exactKeys(value, keys) {
  return Object.keys(value).every((key) => keys.includes(key));
}

function pngDimensions(data) {
  if (data.length < 24 || !data.subarray(0, 8).equals(PNG_SIGNATURE)) throw new Error("SOURCE_PNG_INVALID");
  const width = data.readUInt32BE(16);
  const height = data.readUInt32BE(20);
  if (!width || !height || width * height > MAX_PIXELS) throw new Error("SOURCE_PNG_DIMENSIONS_INVALID");
  return { width, height };
}

export function validateCreate(value) {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error("INVALID_REQUEST");
  if (!exactKeys(value, ["requestId", "sourcePngBase64", "seed"])) throw new Error("UNSUPPORTED_FIELD");
  if (typeof value.requestId !== "string" || !REQUEST_ID.test(value.requestId)) throw new Error("INVALID_REQUEST");
  if (typeof value.sourcePngBase64 !== "string" || value.sourcePngBase64.length < 8) throw new Error("INVALID_SOURCE");
  if (!Number.isSafeInteger(value.seed) || value.seed < 1 || value.seed > 2_147_483_647) throw new Error("INVALID_SEED");
  const source = Buffer.from(value.sourcePngBase64, "base64");
  if (!source.length || source.length > MAX_SOURCE) throw new Error("SOURCE_SIZE_INVALID");
  pngDimensions(source);
  return { requestId: value.requestId, source, seed: value.seed };
}

function atomicJson(path, value) {
  mkdirSync(dirname(path), { recursive: true, mode: 0o700 });
  const temp = `${path}.${process.pid}.${randomUUID()}.tmp`;
  writeFileSync(temp, `${JSON.stringify(value)}\n`, { mode: 0o600 });
  renameSync(temp, path);
  try { chmodSync(path, 0o600); } catch { /* best effort */ }
}

function stripJob(job) {
  return {
    jobId: job.id,
    requestId: job.requestId,
    status: job.status,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    error: job.error ? { code: job.error } : undefined,
    file: job.status === "succeeded" ? {
      mediaType: "video/mp4", bytes: job.artifactBytes, sha256: job.artifactSha256,
    } : undefined,
  };
}

function createProjectYaml(projectId, seed) {
  return `projectId: ${projectId}\nformat: shorts\ntitle: 펜브러쉬\nbackground:\n  strategy: user-images\n  fit: cover\n  images:\n    - source.png\ndrawing:\n  profile: pen-brush\n  sync: auto\n  # Keep the supplied paper/background clean; paint only the extracted subject.\n  fullBleed: false\n  outlineRatio: 0.38\n  handoffFrames: 8\n  paintEndRatio: 0.88\nambient:\n  scenes: 1\n  cues:\n    - 펜 선화 뒤 브러시 채색\nwidgets: none\noverlays: none\nbgm:\n  mode: \"off\"\nseed: ${seed}\n`;
}

function boundedAppend(current, chunk) {
  const combined = Buffer.concat([current, Buffer.from(chunk)]);
  return combined.length <= MAX_LOG ? combined : combined.subarray(combined.length - MAX_LOG);
}

function isRegular(path) {
  try { return lstatSync(path).isFile() && !lstatSync(path).isSymbolicLink(); }
  catch { return false; }
}

function sha256(path) {
  return createHash("sha256").update(readFileSync(path)).digest("hex");
}

async function mp4Qc(path, maxBytes) {
  if (!isRegular(path)) throw new Error("ARTIFACT_INVALID");
  const size = statSync(path).size;
  if (!size || size > maxBytes) throw new Error("ARTIFACT_SIZE_INVALID");
  const probe = await new Promise((resolvePromise, reject) => {
    const child = spawn("ffprobe", ["-v", "error", "-show_streams", "-show_format", "-of", "json", path], {
      stdio: ["ignore", "pipe", "ignore"], env: { PATH: process.env.PATH || "" },
    });
    const chunks = [];
    child.stdout.on("data", (chunk) => chunks.push(Buffer.from(chunk)));
    child.once("error", reject);
    child.once("close", (code) => code === 0
      ? resolvePromise(JSON.parse(Buffer.concat(chunks).toString("utf8")))
      : reject(new Error("FFPROBE_FAILED")));
  });
  const video = probe.streams?.find((item) => item.codec_type === "video" && item.disposition?.attached_pic !== 1);
  const audio = probe.streams?.find((item) => item.codec_type === "audio");
  const duration = Number(probe.format?.duration);
  if (!video || video.codec_name !== "h264" || video.width !== 1080 || video.height !== 1920 ||
      !Number.isFinite(duration) || duration < 9.9 || duration > 10.1 || !audio || audio.codec_name !== "aac") {
    throw new Error("ARTIFACT_QC_FAILED");
  }
  return { bytes: size, sha256: sha256(path) };
}

function terminate(child) {
  if (!child?.pid) return;
  const pid = process.platform === "win32" ? child.pid : -child.pid;
  try { process.kill(pid, "SIGTERM"); } catch { try { child.kill("SIGTERM"); } catch {} }
  setTimeout(() => { try { process.kill(pid, "SIGKILL"); } catch { try { child.kill("SIGKILL"); } catch {} } }, 1500).unref();
}

export class PenBrushService {
  #jobs = new Map();
  #requestIndex = new Map();
  #children = new Map();
  #closed = false;
  constructor(config) { this.config = config; }
  jobPath(id) { return resolve(this.config.runtimeDir, "jobs", id, "job.json"); }
  workspace(id) { return resolve(this.config.runtimeDir, "jobs", id); }
  indexPath() { return resolve(this.config.runtimeDir, "state", "requests.json"); }
  load() {
    mkdirSync(resolve(this.config.runtimeDir, "jobs"), { recursive: true, mode: 0o700 });
    try {
      const index = JSON.parse(readFileSync(this.indexPath(), "utf8"));
      for (const [requestId, id] of Object.entries(index)) {
        if (!REQUEST_ID.test(requestId) || typeof id !== "string" || !JOB_ID.test(id)) continue;
        try {
          const job = JSON.parse(readFileSync(this.jobPath(id), "utf8"));
          if (job.status === "running" || job.status === "queued") {
            job.status = "failed"; job.error = "BRUSH_RESTARTED"; job.updatedAt = Date.now(); this.save(job);
          }
          this.#requestIndex.set(requestId, id); this.#jobs.set(id, job);
        } catch { /* stale index ignored */ }
      }
    } catch { /* first start */ }
  }
  save(job) {
    this.#jobs.set(job.id, job); atomicJson(this.jobPath(job.id), job);
    this.#requestIndex.set(job.requestId, job.id);
    atomicJson(this.indexPath(), Object.fromEntries(this.#requestIndex));
  }
  ready() {
    return this.config.enabled && existsSync(this.config.pythonBin) && existsSync(resolve(this.config.engineRoot, "bin", "build.py")) &&
      existsSync(resolve(this.config.engineRoot, "node_modules", "@remotion", "cli")) && existsSync(resolve(this.config.browserDir, "browser.json"));
  }
  countPending() { return [...this.#jobs.values()].filter((job) => job.status === "queued" || job.status === "running").length; }
  create(input) {
    const existingId = this.#requestIndex.get(input.requestId);
    if (existingId) return { job: this.#jobs.get(existingId), created: false };
    if (!this.config.enabled) throw new Error("PEN_BRUSH_DISABLED");
    if (!this.ready()) throw new Error("PEN_BRUSH_NOT_READY");
    if (this.countPending() >= this.config.maxPending) throw new Error("PEN_BRUSH_QUEUE_FULL");
    const id = randomUUID(); const now = Date.now();
    const job = { id, requestId: input.requestId, status: "queued", seed: input.seed, createdAt: now, updatedAt: now };
    const workspace = this.workspace(id);
    mkdirSync(resolve(workspace, "input"), { recursive: true, mode: 0o700 });
    writeFileSync(resolve(workspace, "input", "source.png"), input.source, { mode: 0o600 });
    writeFileSync(resolve(workspace, "input", "project.yaml"), createProjectYaml(`pen-brush-${id.slice(0, 12)}`, input.seed), { mode: 0o600 });
    this.save(job); queueMicrotask(() => void this.run(id));
    return { job, created: true };
  }
  async run(id) {
    const job = this.#jobs.get(id);
    if (!job || job.status !== "queued" || this.#closed) return;
    job.status = "running"; job.updatedAt = Date.now(); this.save(job);
    const workspace = this.workspace(id); const logDir = resolve(workspace, "logs");
    await mkdir(logDir, { recursive: true, mode: 0o700 });
    const events = resolve(logDir, "engine-events.jsonl");
    const project = resolve(workspace, "input", "project.yaml");
    let stdout = Buffer.alloc(0); let stderr = Buffer.alloc(0);
    try {
      const child = spawn(this.config.pythonBin, [resolve(this.config.engineRoot, "bin", "build.py"), project,
        "--workspace", workspace, "--audit", "--verify-sources", "--events-jsonl", events], {
        cwd: this.config.engineRoot, detached: process.platform !== "win32", stdio: ["ignore", "pipe", "pipe"],
        env: { PATH: process.env.PATH || "", HOME: process.env.HOME || "", LANG: "ko_KR.UTF-8", PYTHONDONTWRITEBYTECODE: "1",
          DRAW_PROXY_PYTHON: this.config.pythonBin, DRAW_PROXY_ROOT: this.config.root,
          DRAW_PROXY_ENGINE_ROOT: this.config.engineRoot, DRAW_PROXY_MODEL_ROOT: resolve(this.config.runtimeDir, "models") },
      });
      this.#children.set(id, child);
      child.stdout.on("data", (chunk) => { stdout = boundedAppend(stdout, chunk); });
      child.stderr.on("data", (chunk) => { stderr = boundedAppend(stderr, chunk); });
      const result = await new Promise((resolvePromise, reject) => {
        const timer = setTimeout(() => { terminate(child); reject(new Error("PEN_BRUSH_TIMEOUT")); }, this.config.timeoutMs);
        child.once("error", (error) => { clearTimeout(timer); reject(error); });
        child.once("close", (code) => { clearTimeout(timer); resolvePromise(code); });
      });
      if (result !== 0) throw new Error("PEN_BRUSH_RENDER_FAILED");
      if (this.#jobs.get(id)?.status === "cancelled") return;
      const artifact = resolve(workspace, "output", `pen-brush-${id.slice(0, 12)}.mp4`);
      const qc = await mp4Qc(artifact, this.config.maxArtifactBytes);
      job.status = "succeeded"; job.updatedAt = Date.now(); job.finishedAt = Date.now(); job.artifactPath = artifact; job.artifactBytes = qc.bytes; job.artifactSha256 = qc.sha256;
      this.save(job);
    } catch (error) {
      if (this.#jobs.get(id)?.status !== "cancelled") { job.status = "failed"; job.error = String(error.message || error).replace(/[^A-Z0-9_]/g, "_").slice(0, 64); job.updatedAt = Date.now(); job.finishedAt = Date.now(); this.save(job); }
    } finally {
      this.#children.delete(id);
      await writeFile(resolve(logDir, "render.stdout.log"), stdout.subarray(0, MAX_LOG), { mode: 0o600 }).catch(() => {});
      await writeFile(resolve(logDir, "render.stderr.log"), stderr.subarray(0, MAX_LOG), { mode: 0o600 }).catch(() => {});
    }
  }
  get(id) { return this.#jobs.get(id); }
  cancel(id) {
    const job = this.#jobs.get(id); if (!job || !["queued", "running"].includes(job.status)) return false;
    job.status = "cancelled"; job.updatedAt = Date.now(); job.finishedAt = Date.now(); this.save(job); terminate(this.#children.get(id)); return true;
  }
  async close() { this.#closed = true; for (const child of this.#children.values()) terminate(child); }
}

export function createServerContext(config = loadConfig()) {
  const service = new PenBrushService(config); service.load();
  const server = createServer(async (request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    try {
      if (request.method === "GET" && url.pathname === "/health") return json(response, 200, { ok: true, service: "proxy-brush", enabled: config.enabled });
      if (request.method === "GET" && url.pathname === "/ready") return json(response, service.ready() ? 200 : 503, { ready: service.ready() });
      if (!authenticate(request, config)) return json(response, 401, { error: { code: "UNAUTHORIZED" } });
      if (request.method === "POST" && url.pathname === "/internal/v1/pen-brush/jobs") {
        const result = service.create(validateCreate(await readJson(request)));
        return json(response, result.created ? 202 : 200, stripJob(result.job));
      }
      const file = url.pathname.match(/^\/internal\/v1\/pen-brush\/jobs\/([0-9a-f-]+)\/file$/);
      if (request.method === "GET" && file && JOB_ID.test(file[1])) {
        const job = service.get(file[1]);
        if (!job || job.status !== "succeeded" || !job.artifactPath || !isRegular(job.artifactPath)) return json(response, 404, { error: { code: "ARTIFACT_NOT_FOUND" } });
        response.writeHead(200, { "content-type": "video/mp4", "content-length": job.artifactBytes, "cache-control": "no-store", "x-content-type-options": "nosniff" });
        createReadStream(job.artifactPath).pipe(response); return;
      }
      const match = url.pathname.match(/^\/internal\/v1\/pen-brush\/jobs\/([0-9a-f-]+)$/);
      if (match && JOB_ID.test(match[1])) {
        const job = service.get(match[1]);
        if (!job) return json(response, 404, { error: { code: "JOB_NOT_FOUND" } });
        if (request.method === "GET") return json(response, 200, stripJob(job));
        if (request.method === "DELETE") return json(response, service.cancel(job.id) ? 202 : 409, stripJob(job));
      }
      return json(response, 404, { error: { code: "NOT_FOUND" } });
    } catch (error) {
      const code = String(error.message || error).replace(/[^A-Z0-9_]/g, "_").slice(0, 64);
      const status = code === "BODY_TOO_LARGE" ? 413 : code.includes("QUEUE_FULL") ? 429 : code.includes("DISABLED") || code.includes("NOT_READY") ? 503 : 400;
      return json(response, status, { error: { code } });
    }
  });
  return { server, service };
}

if (process.argv.includes("--doctor")) {
  const config = loadConfig();
  const service = new PenBrushService(config); service.load();
  process.stdout.write(`${JSON.stringify({ ready: service.ready(), enabled: config.enabled })}\n`);
  process.exit(service.ready() ? 0 : 1);
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { server, service } = createServerContext();
  server.listen(loadConfig().port, loadConfig().host);
  const stop = () => server.close(() => void service.close().then(() => process.exit(0)));
  process.once("SIGTERM", stop); process.once("SIGINT", stop);
}
