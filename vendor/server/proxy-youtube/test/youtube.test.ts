import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { YoutubeProcessor } from "../src/processor.js";
import {
  createYoutubeRunner,
  canonicalYoutubeUrl,
  kakaoBalancedScaleFilter,
  selectKakaoLiteProfile,
} from "../src/runner.js";
import { createYoutubeServer } from "../src/server.js";
import { YoutubeJobStore } from "../src/store.js";
import { loadYoutubeProxyConfig, type YoutubeProxyConfig } from "../src/config.js";

const root = mkdtempSync(join(tmpdir(), "proxy-youtube-test-"));
const secret = "s".repeat(48);
const config: YoutubeProxyConfig = {
  host:"127.0.0.1",port:0,managerSecretFile:join(root,"secret"),runtimeDir:root,databaseFile:join(root,"db.sqlite3"),
  ytDlpBin:"/bin/true",ffprobeBin:"/usr/bin/true",ffmpegBin:"/usr/bin/true",runnerMode:"fake",requestMaxBytes:8192,maxBytes:1024,kakaoTargetBytes:1024,maxDurationSeconds:900,processTimeoutMs:10000,maxConcurrency:1,maxPendingPerRoom:1,artifactTtlMs:60000,
};
writeFileSync(config.managerSecretFile, secret);

test("quality defaults retain headroom below the Android 50MiB transport guard", () => {
  const loaded = loadYoutubeProxyConfig({}, root);
  assert.equal(loaded.maxBytes, 42 * 1024 * 1024);
  assert.equal(loaded.kakaoTargetBytes, 38 * 1024 * 1024);
  assert.ok(loaded.kakaoTargetBytes < loaded.maxBytes);
  assert.ok(loaded.maxBytes < 50 * 1024 * 1024);
});

test("canonicalizer rejects playlist and accepts video IDs", () => {
  assert.equal(canonicalYoutubeUrl("https://youtu.be/AbCdEfGhI_1"), "https://www.youtube.com/watch?v=AbCdEfGhI_1");
  assert.throws(() => canonicalYoutubeUrl("https://youtube.com/watch?v=AbCdEfGhI_1&list=PL1"));
  assert.throws(() => canonicalYoutubeUrl("https://example.com/AbCdEfGhI_1"));
});

test("quality-balanced profile keeps 480p or 360p detail within the server target", () => {
  const target = 38 * 1024 * 1024;
  const short = selectKakaoLiteProfile(180, target);
  const fiveMinutes = selectKakaoLiteProfile(300, target);
  const medium = selectKakaoLiteProfile(600, target);
  const long = selectKakaoLiteProfile(900, target);
  assert.deepEqual([short.width, short.height, short.portraitWidth, short.portraitHeight, short.fps], [854, 480, 480, 854, 24]);
  assert.deepEqual([fiveMinutes.width, fiveMinutes.height], [854, 480]);
  assert.deepEqual([medium.width, medium.height, medium.portraitWidth, medium.portraitHeight], [640, 360, 360, 640]);
  assert.deepEqual([long.width, long.height, long.portraitWidth, long.portraitHeight], [480, 270, 270, 480]);
  assert.ok(short.videoBitrate >= 1_000_000);
  assert.ok(medium.videoBitrate >= 350_000);
  assert.ok(long.videoBitrate >= 200_000);
  assert.ok([short, fiveMinutes, medium, long].every(profile => profile.estimatedMaximumBytes <= target));
});

test("quality profile changes only after the documented duration boundaries", () => {
  const target = 38 * 1024 * 1024;
  assert.deepEqual(
    [selectKakaoLiteProfile(179, target).width, selectKakaoLiteProfile(180, target).width, selectKakaoLiteProfile(181, target).width],
    [854, 854, 854],
  );
  assert.deepEqual(
    [selectKakaoLiteProfile(300, target).width, selectKakaoLiteProfile(301, target).width, selectKakaoLiteProfile(600, target).width, selectKakaoLiteProfile(601, target).width],
    [854, 640, 640, 480],
  );
});

test("portrait scale retains the portrait long edge with an even H264 dimension", () => {
  const filter = kakaoBalancedScaleFilter(selectKakaoLiteProfile(120, 38 * 1024 * 1024));
  assert.match(filter, /gte\(iw,ih\)/);
  assert.match(filter, /min\(854,iw\)/);
  assert.match(filter, /min\(480,iw\)/);
  assert.match(filter, /min\(854,ih\)/);
  assert.match(filter, /force_divisible_by=2/);
});

test("scoped job returns only validated mp4 to the requesting chat", async () => {
  const store = new YoutubeJobStore(config.databaseFile);
  const processor = new YoutubeProcessor(store, createYoutubeRunner(config), config);
  processor.start();
  const server = createYoutubeServer(config, processor, secret);
  await new Promise<void>(resolve => server.listen(0, "127.0.0.1", resolve));
  const port = (server.address() as {port:number}).port;
  const base = `http://127.0.0.1:${port}`;
  const headers = { authorization:`Bearer ${secret}`, "content-type":"application/json" };
  const input = {requestId:"youtube:101:201",chatId:"101",userId:"301",logId:"201",url:"https://youtu.be/AbCdEfGhI_1"};
  const create = await fetch(`${base}/v1/youtube/jobs`, {method:"POST",headers,body:JSON.stringify(input)});
  assert.equal(create.status,202);
  const first = await create.json() as {jobId:string};
  let status = "queued";
  for(let i=0;i<30&&status!=="succeeded";i++) { await new Promise(r=>setTimeout(r,10)); const response=await fetch(`${base}/v1/youtube/jobs/${first.jobId}?chatId=101`,{headers}); status=(await response.json() as {status:string}).status; }
  assert.equal(status,"succeeded");
  assert.equal((await fetch(`${base}/v1/youtube/jobs/${first.jobId}/file?chatId=999`,{headers})).status,404);
  const file = await fetch(`${base}/v1/youtube/jobs/${first.jobId}/file?chatId=101`,{headers});
  assert.equal(file.status,200); assert.equal(file.headers.get("content-type"),"video/mp4"); assert.equal((await file.arrayBuffer()).byteLength,16);
  await new Promise<void>(resolve => server.close(()=>resolve())); await processor.close();
});
