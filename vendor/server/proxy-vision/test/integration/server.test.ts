import assert from "node:assert/strict";
import { chmodSync,mkdtempSync,mkdirSync,writeFileSync } from "node:fs";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { DatabaseSync } from "node:sqlite";
import { after,before,test } from "node:test";
import { loadVisionConfig } from "../../src/config.js";
import { createVisionServer,type VisionServerContext } from "../../src/server.js";
import { VisionStore } from "../../src/store.js";

const root=mkdtempSync(resolve(tmpdir(),"proxy-vision-test-"));const managerSecret="m".repeat(48);const codexSecret="c".repeat(48);
const originalFetch=globalThis.fetch;let context:VisionServerContext;let baseUrl="";
const png=Buffer.alloc(24);Buffer.from([137,80,78,71,13,10,26,10]).copy(png);
const gif=Buffer.alloc(24);Buffer.from("GIF89a","ascii").copy(gif);const mediaTypes=new Map<string,string>();

before(async()=>{
  mkdirSync(resolve(root,"runtime/secrets"),{recursive:true});
  writeFileSync(resolve(root,"runtime/secrets/manager.secret"),managerSecret);
  writeFileSync(resolve(root,"runtime/secrets/codex-upstream.secret"),codexSecret);
  const ffmpeg=resolve(root,"fake-ffmpeg.mjs");
  writeFileSync(ffmpeg,`#!/usr/bin/env node\nprocess.stdin.resume();process.stdin.on("end",()=>process.stdout.write(Buffer.from([${[...png].join(",")}])));\n`);
  chmodSync(ffmpeg,0o700);
  globalThis.fetch=(async(input: string|URL|Request,init?:RequestInit)=>{
    const url=String(input instanceof Request?input.url:input);
    if(url.endsWith("/ready"))return new Response(JSON.stringify({ready:true}),{status:200,headers:{"content-type":"application/json"}});
    if(url.startsWith("https://talk.kakaocdn.net/")&&url.includes("headerless.gif"))return new Response(new Uint8Array(gif),{status:200,headers:{"content-length":String(gif.length)}});
    if(url.startsWith("https://talk.kakaocdn.net/"))return new Response(new Uint8Array(png),{status:200,headers:{"content-type":"image/png","content-length":String(png.length)}});
    if(url.endsWith("/internal/v1/codex/vision/analyze")){
      assert.equal((init?.headers as Record<string,string>)["x-heybot-service-id"],"vision");
      const headers=init?.headers as Record<string,string>;const task=headers["x-heybot-vision-task"]??"";
      assert.ok(["describe","ocr","translate_ko"].includes(task));
      mediaTypes.set(headers["x-request-id"]??"",headers["content-type"]??"");
      return new Response(JSON.stringify({requestId:headers["x-request-id"],result:{version:2,task,answer:`ANSWER_${task}`,visibleObjects:["로봇"],extractedText:task==="describe"?[]:["HELLO"],uncertainty:"low"}}),{status:200,headers:{"content-type":"application/json"}});
    }
    throw new Error(`unexpected fetch ${url}`);
  }) as typeof fetch;
  const config=loadVisionConfig({
    VISION_PROXY_RUNTIME_DIR:"./runtime",
    VISION_PROXY_MANAGER_SECRET_FILE:"./runtime/secrets/manager.secret",
    VISION_PROXY_CODEX_SECRET_FILE:"./runtime/secrets/codex-upstream.secret",
    VISION_PROXY_FFMPEG_COMMAND:ffmpeg,
  },root);
  context=createVisionServer(config);await new Promise<void>(resolvePromise=>context.server.listen(0,"127.0.0.1",resolvePromise));
  baseUrl=`http://127.0.0.1:${(context.server.address() as AddressInfo).port}`;
});

after(async()=>{globalThis.fetch=originalFetch;context.server.closeIdleConnections();context.server.closeAllConnections();await new Promise<void>((resolvePromise,reject)=>context.server.close(error=>error?reject(error):resolvePromise()));await context.shutdown();});

async function wait(id:string):Promise<any>{for(let i=0;i<100;i+=1){const response=await originalFetch(`${baseUrl}/v1/vision/jobs/${id}?chatId=10`,{headers:{authorization:`Bearer ${managerSecret}`}});const body=await response.json() as any;if(body.status==="succeeded")return body;await new Promise(r=>setTimeout(r,10));}throw new Error("timeout");}

test("authenticated durable job analyzes exact scoped source without exposing URL",async()=>{
  const request={requestId:"vision:10:20:ocr",chatId:"10",userId:"30",logId:"20",task:"ocr",source:{url:"https://talk.kakaocdn.net/fake/image.png?fixture=1",width:100,height:100,declaredBytes:png.length,expiresAtMillis:Date.now()+60_000}};
  const unauthorized=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(unauthorized.status,401);
  const created=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(created.status,202);const initial=await created.json() as any;assert.equal(JSON.stringify(initial).includes("kakaocdn"),false);
  const repeated=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(repeated.status,200);assert.equal((await repeated.json() as any).jobId,initial.jobId);
  const crossRoom=await originalFetch(`${baseUrl}/v1/vision/jobs/${initial.jobId}?chatId=11`,{headers:{authorization:`Bearer ${managerSecret}`}});assert.equal(crossRoom.status,404);
  const completed=await wait(initial.jobId);assert.equal(completed.result.version,2);assert.equal(completed.result.task,"ocr");assert.equal(completed.result.answer,"ANSWER_ocr");
});

test("same source creates independent describe, OCR, and translation jobs",async()=>{
  const jobs:any[]=[];
  for(const task of ["describe","ocr","translate_ko"]){
    const request={requestId:`vision:10:22:${task}`,chatId:"10",userId:"30",logId:"22",task,source:{url:`https://talk.kakaocdn.net/fake/${task}.png`,width:100,height:100,declaredBytes:png.length,expiresAtMillis:Date.now()+60_000}};
    const response=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});
    assert.equal(response.status,202);jobs.push(await response.json());
  }
  assert.equal(new Set(jobs.map(job=>job.jobId)).size,3);
  const completed=await Promise.all(jobs.map(job=>wait(job.jobId)));
  assert.deepEqual(completed.map(job=>job.result.task),["describe","ocr","translate_ko"]);
});

test("headerless animated GIF is normalized to PNG before analysis",async()=>{
  const request={requestId:"vision:10:25:describe",chatId:"10",userId:"30",logId:"25",task:"describe",source:{url:"https://talk.kakaocdn.net/fake/headerless.gif",width:100,height:100,declaredBytes:gif.length,expiresAtMillis:Date.now()+60_000}};
  const response=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});
  assert.equal(response.status,202);const created=await response.json() as any;const completed=await wait(created.jobId);
  assert.equal(completed.status,"succeeded");assert.equal(mediaTypes.get(request.requestId),"image/png");
});

test("a terminal failed job is safely requeued with the refreshed source",async()=>{
  const request={requestId:"vision:10:26:describe",chatId:"10",userId:"30",logId:"26",task:"describe" as const,source:{url:"https://talk.kakaocdn.net/fake/retry.png",width:100,height:100,declaredBytes:png.length,expiresAtMillis:Date.now()+60_000}};
  const failed=context.store.create(request);assert.equal(context.store.markRunning(failed.id),true);context.store.fail(failed.id,"INVALID_IMAGE");
  const response=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});
  assert.equal(response.status,202);const retried=await response.json() as any;assert.equal(retried.jobId,failed.id);
  const completed=await wait(failed.id);assert.equal(completed.status,"succeeded");
});

test("rejects invalid task and conflicting idempotency metadata",async()=>{
  const source={url:"https://talk.kakaocdn.net/fake/conflict.png",width:100,height:100,declaredBytes:png.length,expiresAtMillis:Date.now()+60_000};
  const invalid=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify({requestId:"vision:10:23:bad",chatId:"10",userId:"30",logId:"23",task:"free_prompt",source})});
  assert.equal(invalid.status,400);assert.equal((await invalid.json() as any).error.code,"INVALID_VISION_TASK");
  const initial={requestId:"vision:10:24:ocr",chatId:"10",userId:"30",logId:"24",task:"ocr",source};
  const created=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(initial)});assert.equal(created.status,202);
  const conflict=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify({...initial,task:"describe"})});
  assert.equal(conflict.status,409);assert.equal((await conflict.json() as any).error.code,"VISION_REQUEST_CONFLICT");
});

test("migrates legacy durable store with describe task default",()=>{
  const path=resolve(root,"legacy/vision.sqlite3");mkdirSync(resolve(root,"legacy"),{recursive:true});
  const legacy=new DatabaseSync(path);legacy.exec(`CREATE TABLE jobs (sequence INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT UNIQUE NOT NULL,request_id TEXT UNIQUE NOT NULL,chat_id TEXT NOT NULL,user_id TEXT NOT NULL,log_id TEXT NOT NULL,source_url TEXT,source_width INTEGER NOT NULL,source_height INTEGER NOT NULL,source_bytes INTEGER NOT NULL,source_expires INTEGER NOT NULL,status TEXT NOT NULL,created_at INTEGER NOT NULL,updated_at INTEGER NOT NULL,error_code TEXT,result_json TEXT)`);legacy.close();
  const migrated=new VisionStore(path);migrated.close();const inspection=new DatabaseSync(path);const columns=inspection.prepare("PRAGMA table_info(jobs)").all() as any[];assert.ok(columns.some(column=>column.name==="task"));inspection.close();
});

test("rejects non-allowlisted source before queueing",async()=>{
  const response=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify({requestId:"vision:10:21",chatId:"10",userId:"30",logId:"21",source:{url:"https://evil.example/x.png",width:100,height:100,declaredBytes:24,expiresAtMillis:Date.now()+60_000}})});
  assert.equal(response.status,400);assert.equal((await response.json() as any).error.code,"FORBIDDEN_SOURCE");
});
