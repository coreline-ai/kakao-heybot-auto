import assert from 'node:assert/strict';
import { mkdtemp, mkdir, realpath, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { test } from 'node:test';
import { locateSessionVideo, videoGenerationInstruction } from '../../src/cli/runner.js';

test('locates exactly one MP4 in the confined Grok session directory', async (t) => {
  const root=await mkdtemp(join(tmpdir(),'proxy-grok-runner-'));
  const workspace=join(root,'workspace');
  const sessionId='019f9ba7-06b5-7482-869b-26a7711bfb11';
  const videoDir=join(root,encodeURIComponent(workspace),sessionId,'videos');
  await mkdir(videoDir,{recursive:true});
  const video=join(videoDir,'1.mp4');
  await writeFile(video,Buffer.alloc(32,1));
  t.after(async()=>rm(root,{recursive:true,force:true}));
  assert.equal(locateSessionVideo(root,workspace,sessionId),await realpath(video));
});

test('rejects ambiguous session video artifacts', async (t) => {
  const root=await mkdtemp(join(tmpdir(),'proxy-grok-runner-'));
  const workspace=join(root,'workspace');
  const sessionId='019f9ba7-06b5-7482-869b-26a7711bfb11';
  const videoDir=join(root,encodeURIComponent(workspace),sessionId,'videos');
  await mkdir(videoDir,{recursive:true});
  await Promise.all(['1.mp4','2.mp4'].map((name)=>writeFile(join(videoDir,name),Buffer.alloc(32,1))));
  t.after(async()=>rm(root,{recursive:true,force:true}));
  assert.throws(()=>locateSessionVideo(root,workspace,sessionId),/GROK_ARTIFACT_CONTRACT/);
});

test('video instruction fixes the image-to-video workflow without allowing research detours', () => {
  const instruction=videoGenerationInstruction('핑크 로봇이 손을 흔든다');
  assert.match(instruction,/image_gen once/);
  assert.match(instruction,/image_to_video once/);
  assert.match(instruction,/duration 6 seconds/);
  assert.match(instruction,/Do not read skills, help, files, or terminal output/);
});
