import assert from "node:assert/strict";
import { mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import test from "node:test";
import { type AddressInfo } from "node:net";
import { type Server } from "node:http";
import { AudioJobStore } from "../src/store.js";
import { assertAudioMagic, DefaultAudioPipeline, whisperArguments } from "../src/pipeline.js";
import { createAudioServer } from "../src/server.js";
import type { AudioProxyConfig } from "../src/config.js";
import type { AudioProcessorPort } from "../src/processor.js";
import type { AudioCreateInput, PublicAudioJob } from "../src/types.js";

test("audio magic is extension-bound", () => {
  assert.doesNotThrow(() => assertAudioMagic(Buffer.from("ID3fixture00"), "mp3"));
  assert.doesNotThrow(() => assertAudioMagic(Buffer.from([0xff, 0xfb, 0x90, 0x64]), "mp3"));
  assert.doesNotThrow(() => assertAudioMagic(Buffer.from("0000ftypM4A "), "m4a"));
  assert.doesNotThrow(() => assertAudioMagic(Buffer.from("RIFF0000WAVE"), "wav"));
  assert.throws(() => assertAudioMagic(Buffer.from("PK0304000000"), "mp3"), /AUDIO_MAGIC_MISMATCH/);
  assert.throws(() => assertAudioMagic(Buffer.from("ID3fixture00"), "wav"), /AUDIO_MAGIC_MISMATCH/);
});

test("whisper JSON keeps real evidence timestamps", () => {
  const args = whisperArguments("model.bin", "out", "audio.wav");
  assert.equal(args.includes("-nt"), false);
  assert.equal(args.includes("--no-timestamps"), false);
  assert.equal(args.includes("-oj"), true);
});

test("durable store is request-idempotent, chat scoped, encrypted and purgeable", () => {
  const root = mkdtempSync(resolve(tmpdir(), "heybot-audio-"));
  const key = resolve(root, "key"); writeFileSync(key, "11".repeat(32), { mode: 0o600 });
  const store = new AudioJobStore(resolve(root, "jobs.sqlite3"), key);
  const input = { requestId: "audio:10:20", chatId: "10", language: "ko" as const,
    source: { url: "https://talk.kakaocdn.net/fake", declaredBytes: 1000, expiresAtMillis: Date.now() + 60_000, declaredExtension: "m4a" as const } };
  const first = store.createOrGet(input); const second = store.createOrGet(input);
  assert.equal(first.created, true); assert.equal(second.created, false); assert.equal(first.job.id, second.job.id);
  assert.throws(() => store.createOrGet({ ...input, chatId: "11" }), /REQUEST_SCOPE_MISMATCH/);
  const claimed = store.claim(first.job.id)!; assert.equal(claimed.sourceUrl, input.source.url); assert.equal(store.get(first.job.id)!.sourceUrl, null);
  store.succeed(first.job.id, { version: 1, status: "transcribed", durationMs: 1000, language: "ko", segments: [{ id: "S0001", startMs: 0, endMs: 900, text: "fixture" }], quality: { speechRatio: 0.9, warnings: [] } });
  assert.equal(store.get(first.job.id)!.result!.segments[0]!.text, "fixture");
  const bytes = readFileSync(resolve(root, "jobs.sqlite3"));
  assert.equal(bytes.includes(Buffer.from("fixture")), false);
  assert.equal(store.purge(first.job.id, "11"), false); assert.equal(store.purge(first.job.id, "10"), true);
  store.close();
});

test("restart fails a claimed job closed and TTL cleanup removes terminal ciphertext", () => {
  const root = mkdtempSync(resolve(tmpdir(), "heybot-audio-restart-"));
  const key = resolve(root, "key"); writeFileSync(key, "22".repeat(32), { mode: 0o600 });
  const db = resolve(root, "jobs.sqlite3");
  const input = { requestId: "audio:10:restart", chatId: "10", language: "ko" as const,
    source: { url: "https://talk.kakaocdn.net/fake", declaredBytes: 1000, expiresAtMillis: Date.now() + 60_000, declaredExtension: "wav" as const } };
  const first = new AudioJobStore(db, key);
  const created = first.createOrGet(input).job;
  assert.equal(first.claim(created.id)!.status, "fetching");
  first.close();

  const restored = new AudioJobStore(db, key);
  const failed = restored.get(created.id)!;
  assert.equal(failed.status, "failed");
  assert.equal(failed.errorCode, "AUDIO_WORKER_RESTARTED");
  assert.equal(failed.sourceUrl, null);
  assert.equal(restored.cleanup(Date.now() + 1_000), 1);
  assert.equal(restored.get(created.id), undefined);
  restored.close();
});

async function listen(server: Server): Promise<string> {
  await new Promise<void>((done) => server.listen(0, "127.0.0.1", done));
  return `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
}

test("HTTP contract authenticates, validates Kakao source and scopes jobs by chat", async (t) => {
  const root = mkdtempSync(resolve(tmpdir(), "heybot-audio-http-"));
  const secret = "a".repeat(48);
  const secretFile = resolve(root, "manager.secret");
  writeFileSync(secretFile, secret);
  const job: PublicAudioJob = {
    version: 1, jobId: "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
    requestId: "audio:10:20", chatId: "10", status: "queued",
  };
  const processor: AudioProcessorPort = {
    create(input: AudioCreateInput) { return { job: { ...job, requestId: input.requestId }, created: true }; },
    get(id: string, chatId: string) { return id === job.jobId && chatId === "10" ? job : undefined; },
    cancel(id: string, chatId: string) { return this.get(id, chatId); },
    purge(id: string, chatId: string) { return id === job.jobId && chatId === "10"; },
    async readiness() { return { ready: true, version: "fixture" }; },
    async close() {},
  };
  const config = {
    host: "127.0.0.1", port: 0, managerSecretFile: secretFile,
    transcriptKeyFile: resolve(root, "key"), runtimeDir: root,
    databaseFile: resolve(root, "db.sqlite3"), runnerMode: "fake",
    ffmpegBin: "ffmpeg", ffprobeBin: "ffprobe", whisperBin: "whisper-cli",
    whisperModel: resolve(root, "model"), requestMaxBytes: 32_768,
    sourceMaxBytes: 100_000, sourceTimeoutMs: 10_000, processTimeoutMs: 10_000,
    maxDurationMs: 60_000, maxConcurrency: 1, maxPendingPerRoom: 1,
    transcriptTtlMs: 60_000,
  } satisfies AudioProxyConfig;
  const server = createAudioServer(config, processor);
  const base = await listen(server);
  t.after(() => new Promise<void>((done) => server.close(() => done())));
  const body = JSON.stringify({
    requestId: "audio:10:20", chatId: "10", language: "ko",
    source: {
      url: "https://talk.kakaocdn.net/file.m4a", declaredBytes: 100,
      expiresAtMillis: Date.now() + 60_000, declaredExtension: "m4a",
    },
  });
  assert.equal((await fetch(`${base}/v1/audio/transcriptions`, { method: "POST", body })).status, 401);
  const created = await fetch(`${base}/v1/audio/transcriptions`, {
    method: "POST", body,
    headers: { authorization: `Bearer ${secret}`, "content-type": "application/json" },
  });
  assert.equal(created.status, 202);
  assert.equal((await created.json() as PublicAudioJob).chatId, "10");
  const forbidden = await fetch(`${base}/v1/audio/transcriptions`, {
    method: "POST", body: body.replace("talk.kakaocdn.net", "example.com"),
    headers: { authorization: `Bearer ${secret}`, "content-type": "application/json" },
  });
  assert.equal(forbidden.status, 403);
  assert.equal((await fetch(`${base}/v1/audio/transcriptions/${job.jobId}?chatId=11`, {
    headers: { authorization: `Bearer ${secret}` },
  })).status, 404);
});

test("production readiness fails closed on model checksum mismatch", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "heybot-audio-ready-"));
  const model = resolve(root, "model.bin");
  writeFileSync(model, "not-a-real-model");
  const config = {
    host: "127.0.0.1", port: 4363, managerSecretFile: resolve(root, "manager.secret"),
    transcriptKeyFile: resolve(root, "key"), runtimeDir: root,
    databaseFile: resolve(root, "db.sqlite3"), runnerMode: "cli",
    ffmpegBin: "ffmpeg", ffprobeBin: "ffprobe", whisperBin: "whisper-cli",
    whisperModel: model, whisperModelSha256: "00".repeat(32), requestMaxBytes: 32_768,
    sourceMaxBytes: 100_000, sourceTimeoutMs: 10_000, processTimeoutMs: 10_000,
    maxDurationMs: 60_000, maxConcurrency: 1, maxPendingPerRoom: 1,
    transcriptTtlMs: 60_000,
  } satisfies AudioProxyConfig;
  assert.deepEqual(await new DefaultAudioPipeline(config).readiness(), {
    ready: false, reason: "WHISPER_MODEL_SHA256_MISMATCH",
  });
});

test("expired readiness serves the verified cache while one model hash refresh runs", async () => {
  const root = mkdtempSync(resolve(tmpdir(), "heybot-audio-ready-refresh-"));
  const model = resolve(root, "model.bin");
  writeFileSync(model, "fixture-model");
  const expected = "ab".repeat(32);
  let calls = 0;
  let finishRefresh: ((value: string) => void) | undefined;
  const hasher = async (): Promise<string> => {
    calls += 1;
    if (calls === 1) return expected;
    return await new Promise<string>((resolveHash) => { finishRefresh = resolveHash; });
  };
  const config = {
    host: "127.0.0.1", port: 4363, managerSecretFile: resolve(root, "manager.secret"),
    transcriptKeyFile: resolve(root, "key"), runtimeDir: root,
    databaseFile: resolve(root, "db.sqlite3"), runnerMode: "cli",
    ffmpegBin: "/usr/bin/true", ffprobeBin: "/usr/bin/true", whisperBin: "/usr/bin/true",
    whisperModel: model, whisperModelSha256: expected, requestMaxBytes: 32_768,
    sourceMaxBytes: 100_000, sourceTimeoutMs: 10_000, processTimeoutMs: 10_000,
    maxDurationMs: 60_000, maxConcurrency: 1, maxPendingPerRoom: 1,
    transcriptTtlMs: 60_000,
  } satisfies AudioProxyConfig;
  const pipeline = new DefaultAudioPipeline(config, hasher, 1);
  assert.equal((await pipeline.readiness()).ready, true);
  await new Promise((resolveWait) => setTimeout(resolveWait, 5));
  assert.equal((await pipeline.readiness()).ready, true);
  assert.equal(calls, 2);
  finishRefresh?.(expected);
  await new Promise((resolveWait) => setTimeout(resolveWait, 20));
  assert.equal((await pipeline.readiness()).ready, true);
});
