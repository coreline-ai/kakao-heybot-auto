import assert from "node:assert/strict";
import { mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";
import { YoutubeProcessor } from "../src/processor.js";
import { createYoutubeRunner, canonicalYoutubeUrl, selectKakaoLiteProfile } from "../src/runner.js";
import { createYoutubeServer } from "../src/server.js";
import { YoutubeJobStore } from "../src/store.js";
import type { YoutubeProxyConfig } from "../src/config.js";

const root = mkdtempSync(join(tmpdir(), "proxy-youtube-test-"));
const secret = "s".repeat(48);
const config: YoutubeProxyConfig = {
  host:"127.0.0.1",port:0,managerSecretFile:join(root,"secret"),runtimeDir:root,databaseFile:join(root,"db.sqlite3"),
  ytDlpBin:"/bin/true",ffprobeBin:"/usr/bin/true",ffmpegBin:"/usr/bin/true",runnerMode:"fake",requestMaxBytes:8192,maxBytes:1024,kakaoTargetBytes:1024,maxDurationSeconds:900,processTimeoutMs:10000,maxConcurrency:1,maxPendingPerRoom:1,artifactTtlMs:60000,
};
writeFileSync(config.managerSecretFile, secret);

test("canonicalizer rejects playlist and accepts video IDs", () => {
  assert.equal(canonicalYoutubeUrl("https://youtu.be/AbCdEfGhI_1"), "https://www.youtube.com/watch?v=AbCdEfGhI_1");
  assert.throws(() => canonicalYoutubeUrl("https://youtube.com/watch?v=AbCdEfGhI_1&list=PL1"));
  assert.throws(() => canonicalYoutubeUrl("https://example.com/AbCdEfGhI_1"));
});

test("Kakao-lite profile gets smaller for longer videos and stays under target", () => {
  const target = 18 * 1024 * 1024;
  const short = selectKakaoLiteProfile(120, target);
  const medium = selectKakaoLiteProfile(480, target);
  const long = selectKakaoLiteProfile(900, target);
  assert.deepEqual([short.width, short.height, short.fps], [480, 270, 24]);
  assert.deepEqual([medium.width, medium.height], [426, 240]);
  assert.deepEqual([long.width, long.height], [320, 180]);
  assert.ok(short.maxVideoBitrate > medium.maxVideoBitrate);
  assert.ok(medium.maxVideoBitrate > long.maxVideoBitrate);
  assert.ok(long.estimatedMaximumBytes <= target);
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
