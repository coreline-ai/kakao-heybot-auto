import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { test } from "node:test";
import { loadVisionConfig } from "../../src/config.js";
import { normalizeVisionImage } from "../../src/normalize.js";

test("animated GIF is reduced to a bounded PNG frame before Codex",async()=>{
  const root=mkdtempSync(resolve(tmpdir(),"vision-normalize-test-"));
  const command=resolve(root,"fake-ffmpeg.mjs");
  const png=[137,80,78,71,13,10,26,10,0,0,0,0];
  writeFileSync(command,`#!/usr/bin/env node\nprocess.stdin.resume();process.stdin.on("end",()=>process.stdout.write(Buffer.from([${png.join(",")}])));\n`);
  chmodSync(command,0o700);
  const config=loadVisionConfig({VISION_PROXY_FFMPEG_COMMAND:command},root);
  const gif=Buffer.from("GIF89a fake animated payload","ascii");

  const normalized=await normalizeVisionImage(
    {data:gif,mediaType:"image/gif"},config,new AbortController().signal,
  );

  assert.equal(normalized.mediaType,"image/png");
  assert.deepEqual([...normalized.data.subarray(0,8)],png.slice(0,8));
});
