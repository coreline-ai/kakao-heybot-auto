import { createReadStream, createWriteStream, existsSync, mkdirSync, readFileSync, rmSync, statSync } from "node:fs";
import { createHash } from "node:crypto";
import { request as httpsRequest } from "node:https";
import { spawn } from "node:child_process";
import { resolve } from "node:path";
import type { AudioProxyConfig } from "./config.js";
import type { AudioJobStatus, AudioSegment, AudioTranscriptResult, StoredAudioJob } from "./types.js";

export function assertAudioMagic(data: Buffer, extension: string): void {
  const mp3 = data.subarray(0, 3).toString("ascii") === "ID3" || (data[0] === 0xff && ((data[1] ?? 0) & 0xe0) === 0xe0);
  const m4a = data.length >= 12 && data.subarray(4, 8).toString("ascii") === "ftyp";
  const wav = data.subarray(0, 4).toString("ascii") === "RIFF" && data.subarray(8, 12).toString("ascii") === "WAVE";
  if ((extension === "mp3" && mp3) || (extension === "m4a" && m4a) || (extension === "wav" && wav)) return;
  throw new Error("AUDIO_MAGIC_MISMATCH");
}

async function sha256(path: string): Promise<string> {
  const hash = createHash("sha256");
  for await (const chunk of createReadStream(path)) hash.update(chunk as Buffer);
  return hash.digest("hex");
}

async function download(job: StoredAudioJob, target: string, config: AudioProxyConfig, signal: AbortSignal): Promise<void> {
  if (!job.sourceUrl) throw new Error("AUDIO_SOURCE_REFERENCE_LOST");
  const url = new URL(job.sourceUrl);
  if (url.protocol !== "https:" || url.hostname.toLowerCase() !== "talk.kakaocdn.net" || url.username || url.password || (url.port && url.port !== "443")) {
    throw new Error("FORBIDDEN_SOURCE");
  }
  if (job.expiresAtMillis <= Date.now()) throw new Error("SOURCE_EXPIRED");
  await new Promise<void>((resolvePromise, reject) => {
    let bytes = 0;
    const output = createWriteStream(target, { mode: 0o600 });
    const request = httpsRequest(url, { method: "GET", signal, timeout: config.sourceTimeoutMs }, (response) => {
      if ((response.statusCode ?? 0) >= 300 && (response.statusCode ?? 0) < 400) {
        response.resume(); reject(new Error("SOURCE_REDIRECT_FORBIDDEN")); return;
      }
      if (response.statusCode !== 200) { response.resume(); reject(new Error(`SOURCE_HTTP_${response.statusCode ?? 0}`)); return; }
      const declared = Number(response.headers["content-length"] ?? 0);
      if (Number.isFinite(declared) && declared > config.sourceMaxBytes) { response.resume(); reject(new Error("SOURCE_TOO_LARGE")); return; }
      response.on("data", (chunk: Buffer) => {
        bytes += chunk.length;
        if (bytes > config.sourceMaxBytes) request.destroy(new Error("SOURCE_TOO_LARGE"));
      });
      response.pipe(output);
      output.once("finish", () => output.close(() => resolvePromise()));
    });
    request.once("timeout", () => request.destroy(new Error("SOURCE_TIMEOUT")));
    request.once("error", reject);
    output.once("error", reject);
    request.end();
  });
  const bytes = statSync(target).size;
  if (bytes < 12) throw new Error("INVALID_AUDIO");
  if (job.declaredBytes > 0 && Math.abs(bytes - job.declaredBytes) > Math.max(4096, job.declaredBytes * 0.1)) {
    throw new Error("SOURCE_SIZE_MISMATCH");
  }
}

async function runProcess(command: string, args: string[], timeoutMs: number, signal: AbortSignal): Promise<string> {
  return await new Promise((resolvePromise, reject) => {
    const child = spawn(command, args, { detached: true, stdio: ["ignore", "pipe", "ignore"] });
    let stdout = "";
    let timedOut = false;
    const kill = (sig: NodeJS.Signals): void => {
      if (!child.pid) return;
      try { process.kill(-child.pid, sig); } catch { child.kill(sig); }
    };
    const timeout = setTimeout(() => { timedOut = true; kill("SIGTERM"); setTimeout(() => kill("SIGKILL"), 2_000).unref(); }, timeoutMs);
    const abort = (): void => { kill("SIGTERM"); setTimeout(() => kill("SIGKILL"), 2_000).unref(); };
    signal.addEventListener("abort", abort, { once: true });
    child.stdout.on("data", (chunk: Buffer) => { if (stdout.length < 2_000_000) stdout += chunk.toString("utf8"); });
    child.once("error", reject);
    child.once("close", (code) => {
      clearTimeout(timeout); signal.removeEventListener("abort", abort);
      if (signal.aborted) return reject(new Error("AUDIO_CANCELLED"));
      if (timedOut) return reject(new Error("AUDIO_PROCESS_TIMEOUT"));
      if (code !== 0) return reject(new Error("AUDIO_PROCESS_EXIT_NONZERO"));
      resolvePromise(stdout);
    });
  });
}

interface ProbeResult { durationMs: number; }

async function validateMedia(source: string, extension: string, config: AudioProxyConfig, signal: AbortSignal): Promise<ProbeResult> {
  assertAudioMagic(readFileSync(source).subarray(0, 32), extension);
  const raw = await runProcess(config.ffprobeBin, ["-v", "error", "-show_streams", "-show_format", "-of", "json", source], 30_000, signal);
  const body = JSON.parse(raw) as { streams?: Array<Record<string, unknown>>; format?: Record<string, unknown> };
  const audio = (body.streams ?? []).filter((stream) => stream.codec_type === "audio");
  const video = (body.streams ?? []).filter((stream) => stream.codec_type === "video");
  if (audio.length !== 1 || video.length !== 0) throw new Error("UNSUPPORTED_AUDIO_STREAMS");
  const durationSeconds = Number(audio[0]?.duration ?? body.format?.duration);
  if (!Number.isFinite(durationSeconds) || durationSeconds <= 0) throw new Error("INVALID_AUDIO_DURATION");
  const durationMs = Math.round(durationSeconds * 1000);
  if (durationMs > config.maxDurationMs) throw new Error("AUDIO_DURATION_LIMIT");
  return { durationMs };
}

function parseWhisperJson(value: unknown, durationMs: number): AudioTranscriptResult {
  if (!value || typeof value !== "object") throw new Error("STT_OUTPUT_INVALID");
  const body = value as Record<string, unknown>;
  const rawSegments = Array.isArray(body.transcription) ? body.transcription : Array.isArray(body.segments) ? body.segments : [];
  const segments: AudioSegment[] = rawSegments.slice(0, 2_000).map((item, index) => {
    if (!item || typeof item !== "object") throw new Error("STT_OUTPUT_INVALID");
    const segment = item as Record<string, unknown>;
    const offsets = (segment.offsets && typeof segment.offsets === "object" ? segment.offsets : {}) as Record<string, unknown>;
    const timestamps = (segment.timestamps && typeof segment.timestamps === "object" ? segment.timestamps : {}) as Record<string, unknown>;
    const startMs = Number(segment.startMs ?? offsets.from ?? timestamps.from ?? 0);
    const endMs = Number(segment.endMs ?? offsets.to ?? timestamps.to ?? 0);
    const text = String(segment.text ?? "").trim();
    if (!Number.isFinite(startMs) || !Number.isFinite(endMs) || startMs < 0 || endMs < startMs || endMs > durationMs + 2_000 || !text || text.length > 2_000) throw new Error("STT_OUTPUT_INVALID");
    return { id: `S${String(index + 1).padStart(4, "0")}`, startMs: Math.round(startMs), endMs: Math.round(endMs), text };
  });
  const spoken = segments.reduce((sum, item) => sum + Math.max(0, item.endMs - item.startMs), 0);
  const totalChars = segments.reduce((sum, item) => sum + item.text.length, 0);
  if (totalChars > 200_000) throw new Error("STT_OUTPUT_TOO_LARGE");
  return {
    version: 1, status: "transcribed", durationMs, language: "ko", segments,
    quality: { speechRatio: Math.min(1, Number((spoken / durationMs).toFixed(4))), warnings: segments.length ? [] : ["NO_SPEECH"] },
  };
}

export function whisperArguments(model: string, outputPrefix: string, normalized: string): string[] {
  // Do not pass --no-timestamps/-nt. In whisper.cpp 1.9.x that option emits
  // one 30-second window even for short clips, which violates the real media
  // duration and destroys evidence timestamps.
  return ["-m", model, "-l", "ko", "-oj", "-of", outputPrefix, "-f", normalized, "-np"];
}

export interface AudioPipeline {
  readiness(): Promise<{ ready: boolean; reason?: string; version?: string }>;
  run(job: StoredAudioJob, setStatus: (status: AudioJobStatus) => void, signal: AbortSignal): Promise<AudioTranscriptResult>;
}

type AudioReadiness = { ready: boolean; reason?: string; version?: string };

export class DefaultAudioPipeline implements AudioPipeline {
  #readinessCache?: {
    modelSize: number;
    modelMtimeMs: number;
    checkedAtMillis: number;
    result: AudioReadiness;
  };
  #readinessRefresh?: Promise<AudioReadiness>;

  constructor(
    private readonly config: AudioProxyConfig,
    private readonly modelHasher: (path: string) => Promise<string> = sha256,
    private readonly readinessCacheTtlMs = 5 * 60_000,
  ) {}

  async readiness(): Promise<AudioReadiness> {
    if (this.config.runnerMode === "fake") return { ready: true, version: "fake" };
    if (!existsSync(this.config.whisperModel)) return { ready: false, reason: "WHISPER_MODEL_MISSING" };
    if (!this.config.whisperModelSha256 || !/^[0-9a-f]{64}$/.test(this.config.whisperModelSha256)) {
      return { ready: false, reason: "WHISPER_MODEL_SHA256_MISSING" };
    }
    const model = statSync(this.config.whisperModel);
    const cached = this.#readinessCache;
    if (cached && cached.modelSize === model.size && cached.modelMtimeMs === model.mtimeMs) {
      if (Date.now() - cached.checkedAtMillis >= this.readinessCacheTtlMs) {
        // Hashing the 1.6 GiB model can exceed the manager's 3 s health
        // timeout. Keep serving the last verified result while one shared
        // background refresh revalidates unchanged size/mtime metadata.
        // A changed model never takes this path and is verified fail-closed.
        void this.#refresh(model.size, model.mtimeMs);
      }
      return cached.result;
    }
    return await this.#refresh(model.size, model.mtimeMs);
  }

  #refresh(modelSize: number, modelMtimeMs: number): Promise<AudioReadiness> {
    if (this.#readinessRefresh) return this.#readinessRefresh;
    const refresh = this.#computeReadiness(modelSize, modelMtimeMs);
    this.#readinessRefresh = refresh;
    void refresh.finally(() => {
      if (this.#readinessRefresh === refresh) this.#readinessRefresh = undefined;
    });
    return refresh;
  }

  async #computeReadiness(modelSize: number, modelMtimeMs: number): Promise<AudioReadiness> {
    try {
      if (await this.modelHasher(this.config.whisperModel) !== this.config.whisperModelSha256) {
        const result = { ready: false, reason: "WHISPER_MODEL_SHA256_MISMATCH" };
        this.#readinessCache = {
          modelSize, modelMtimeMs,
          checkedAtMillis: Date.now(), result,
        };
        return result;
      }
      const signal = new AbortController().signal;
      await runProcess(this.config.ffmpegBin, ["-version"], 10_000, signal);
      await runProcess(this.config.ffprobeBin, ["-version"], 10_000, signal);
      const result = await runProcess(this.config.whisperBin, ["--version"], 10_000, signal);
      const ready = { ready: true, version: result.trim().slice(0, 80) || "whisper.cpp" };
      this.#readinessCache = {
        modelSize, modelMtimeMs,
        checkedAtMillis: Date.now(), result: ready,
      };
      return ready;
    } catch {
      const result = { ready: false, reason: "WHISPER_CLI_UNAVAILABLE" };
      this.#readinessCache = {
        modelSize, modelMtimeMs,
        checkedAtMillis: Date.now(), result,
      };
      return result;
    }
  }

  async run(job: StoredAudioJob, setStatus: (status: AudioJobStatus) => void, signal: AbortSignal): Promise<AudioTranscriptResult> {
    const directory = resolve(this.config.runtimeDir, "jobs", job.id);
    mkdirSync(directory, { recursive: true, mode: 0o700 });
    const source = resolve(directory, "source.bin");
    const normalized = resolve(directory, "normalized.wav");
    const outputPrefix = resolve(directory, "transcript");
    try {
      await download(job, source, this.config, signal);
      setStatus("validating");
      const probe = await validateMedia(source, job.declaredExtension, this.config, signal);
      setStatus("normalizing");
      await runProcess(this.config.ffmpegBin, ["-hide_banner", "-loglevel", "error", "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", normalized], 120_000, signal);
      setStatus("transcribing");
      if (this.config.runnerMode === "fake") {
        return parseWhisperJson({ segments: [{ startMs: 0, endMs: Math.min(1_000, probe.durationMs), text: "테스트 음성입니다." }] }, probe.durationMs);
      }
      await runProcess(
        this.config.whisperBin,
        whisperArguments(this.config.whisperModel, outputPrefix, normalized),
        this.config.processTimeoutMs,
        signal,
      );
      const resultPath = `${outputPrefix}.json`;
      if (!existsSync(resultPath)) throw new Error("STT_OUTPUT_MISSING");
      return parseWhisperJson(JSON.parse(readFileSync(resultPath, "utf8")), probe.durationMs);
    } finally {
      rmSync(directory, { recursive: true, force: true });
    }
  }
}
