import assert from "node:assert/strict";
import { mkdtempSync,mkdirSync,writeFileSync } from "node:fs";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import { tmpdir } from "node:os";
import { resolve } from "node:path";
import { after,before,test } from "node:test";
import { loadVisionConfig } from "../../src/config.js";
import { createVisionServer,type VisionServerContext } from "../../src/server.js";

const root=mkdtempSync(resolve(tmpdir(),"proxy-vision-test-"));const managerSecret="m".repeat(48);const codexSecret="c".repeat(48);
const originalFetch=globalThis.fetch;let context:VisionServerContext;let baseUrl="";
const png=Buffer.alloc(24);Buffer.from([137,80,78,71,13,10,26,10]).copy(png);

before(async()=>{
  mkdirSync(resolve(root,"runtime/secrets"),{recursive:true});
  writeFileSync(resolve(root,"runtime/secrets/manager.secret"),managerSecret);
  writeFileSync(resolve(root,"runtime/secrets/codex-upstream.secret"),codexSecret);
  globalThis.fetch=(async(input: string|URL|Request,init?:RequestInit)=>{
    const url=String(input instanceof Request?input.url:input);
    if(url.endsWith("/ready"))return new Response(JSON.stringify({ready:true}),{status:200,headers:{"content-type":"application/json"}});
    if(url.startsWith("https://talk.kakaocdn.net/"))return new Response(new Uint8Array(png),{status:200,headers:{"content-type":"image/png","content-length":String(png.length)}});
    if(url.endsWith("/internal/v1/codex/vision/analyze")){
      assert.equal((init?.headers as Record<string,string>)["x-heybot-service-id"],"vision");
      return new Response(JSON.stringify({requestId:"vision:10:20",result:{version:1,summary:"로봇이 손을 흔들고 있습니다.",visibleObjects:["로봇"],visibleText:[],uncertainty:"low"}}),{status:200,headers:{"content-type":"application/json"}});
    }
    throw new Error(`unexpected fetch ${url}`);
  }) as typeof fetch;
  const config=loadVisionConfig({
    VISION_PROXY_RUNTIME_DIR:"./runtime",
    VISION_PROXY_MANAGER_SECRET_FILE:"./runtime/secrets/manager.secret",
    VISION_PROXY_CODEX_SECRET_FILE:"./runtime/secrets/codex-upstream.secret",
  },root);
  context=createVisionServer(config);await new Promise<void>(resolvePromise=>context.server.listen(0,"127.0.0.1",resolvePromise));
  baseUrl=`http://127.0.0.1:${(context.server.address() as AddressInfo).port}`;
});

after(async()=>{globalThis.fetch=originalFetch;context.server.closeIdleConnections();context.server.closeAllConnections();await new Promise<void>((resolvePromise,reject)=>context.server.close(error=>error?reject(error):resolvePromise()));await context.shutdown();});

async function wait(id:string):Promise<any>{for(let i=0;i<100;i+=1){const response=await originalFetch(`${baseUrl}/v1/vision/jobs/${id}?chatId=10`,{headers:{authorization:`Bearer ${managerSecret}`}});const body=await response.json() as any;if(body.status==="succeeded")return body;await new Promise(r=>setTimeout(r,10));}throw new Error("timeout");}

test("authenticated durable job analyzes exact scoped source without exposing URL",async()=>{
  const request={requestId:"vision:10:20",chatId:"10",userId:"30",logId:"20",source:{url:"https://talk.kakaocdn.net/fake/image.png?fixture=1",width:100,height:100,declaredBytes:png.length,expiresAtMillis:Date.now()+60_000}};
  const unauthorized=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(unauthorized.status,401);
  const created=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(created.status,202);const initial=await created.json() as any;assert.equal(JSON.stringify(initial).includes("kakaocdn"),false);
  const repeated=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify(request)});assert.equal(repeated.status,200);assert.equal((await repeated.json() as any).jobId,initial.jobId);
  const crossRoom=await originalFetch(`${baseUrl}/v1/vision/jobs/${initial.jobId}?chatId=11`,{headers:{authorization:`Bearer ${managerSecret}`}});assert.equal(crossRoom.status,404);
  const completed=await wait(initial.jobId);assert.equal(completed.result.version,1);assert.match(completed.result.summary,/로봇/);
});

test("rejects non-allowlisted source before queueing",async()=>{
  const response=await originalFetch(`${baseUrl}/v1/vision/jobs`,{method:"POST",headers:{authorization:`Bearer ${managerSecret}`,"content-type":"application/json"},body:JSON.stringify({requestId:"vision:10:21",chatId:"10",userId:"30",logId:"21",source:{url:"https://evil.example/x.png",width:100,height:100,declaredBytes:24,expiresAtMillis:Date.now()+60_000}})});
  assert.equal(response.status,400);assert.equal((await response.json() as any).error.code,"FORBIDDEN_SOURCE");
});
