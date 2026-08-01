import { createServer,type IncomingMessage,type Server,type ServerResponse } from "node:http";
import { readFileSync } from "node:fs";
import { timingSafeEqual } from "node:crypto";
import { URL } from "node:url";
import type { VisionConfig } from "./config.js";
import { VisionCodexClient } from "./codex.js";
import { VisionProcessor } from "./processor.js";
import { validateSource } from "./source.js";
import { VisionStore } from "./store.js";
import { publicJob,type CreateVisionJob,type VisionSource } from "./types.js";

function json(response:ServerResponse,status:number,body:unknown):void{const data=Buffer.from(JSON.stringify(body));response.writeHead(status,{"content-type":"application/json; charset=utf-8","content-length":data.length,"cache-control":"no-store"});response.end(data);}
async function readJson(request:IncomingMessage,max:number):Promise<unknown>{const chunks:Buffer[]=[];let bytes=0;for await(const chunk of request){const data=Buffer.from(chunk);bytes+=data.length;if(bytes>max)throw new Error("BODY_TOO_LARGE");chunks.push(data);}try{return JSON.parse(Buffer.concat(chunks).toString("utf8"));}catch{throw new Error("INVALID_JSON");}}
function authenticate(header:string|undefined,secret:string):boolean{if(!header?.startsWith("Bearer "))return false;const supplied=Buffer.from(header.slice(7));const expected=Buffer.from(secret);return supplied.length===expected.length&&timingSafeEqual(supplied,expected);}
function secret(path:string):string{const value=readFileSync(path,"utf8").trim();if(value.length<32||value.length>512)throw new Error("SECRET_INVALID");return value;}
const ID=/^[1-9]\d{0,19}$/;
function validate(value:unknown,config:VisionConfig):CreateVisionJob{
  if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("INVALID_REQUEST");const body=value as Record<string,unknown>;
  if(Object.keys(body).some(key=>!["requestId","chatId","userId","logId","source"].includes(key)))throw new Error("UNSUPPORTED_FIELD");
  if(typeof body.requestId!=="string"||!/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId)||typeof body.chatId!=="string"||!ID.test(body.chatId)||typeof body.userId!=="string"||!ID.test(body.userId)||typeof body.logId!=="string"||!ID.test(body.logId))throw new Error("INVALID_REQUEST");
  if(!body.source||typeof body.source!=="object"||Array.isArray(body.source))throw new Error("INVALID_SOURCE");const raw=body.source as Record<string,unknown>;
  if(Object.keys(raw).some(key=>!["url","width","height","declaredBytes","expiresAtMillis"].includes(key))||typeof raw.url!=="string"||typeof raw.width!=="number"||typeof raw.height!=="number"||typeof raw.declaredBytes!=="number"||typeof raw.expiresAtMillis!=="number")throw new Error("INVALID_SOURCE");
  const source=raw as unknown as VisionSource;validateSource(source,config);return{requestId:body.requestId,chatId:body.chatId,userId:body.userId,logId:body.logId,source};
}
function scoped(store:VisionStore,id:string,url:URL){const chatId=url.searchParams.get("chatId");if(!chatId||!ID.test(chatId))throw new Error("INVALID_CHAT_ID");const job=store.get(id);return job?.chatId===chatId?job:undefined;}
export interface VisionServerContext{server:Server;store:VisionStore;processor:VisionProcessor;shutdown():Promise<void>}
export function createVisionServer(config:VisionConfig):VisionServerContext{
  const managerSecret=secret(config.managerSecretFile);const store=new VisionStore(config.databaseFile);const codex=new VisionCodexClient(config);const processor=new VisionProcessor(store,codex,config);processor.start();
  const server=createServer(async(request,response)=>{const url=new URL(request.url??"/","http://127.0.0.1");try{
    if(request.method==="GET"&&url.pathname==="/health")return json(response,200,{ok:true,service:"proxy-vision"});
    if(request.method==="GET"&&url.pathname==="/ready"){const dependency=await codex.readiness(AbortSignal.timeout(2_000));return json(response,dependency.ready?200:503,{ready:dependency.ready,dependency:{codex:dependency},queue:processor.snapshot()});}
    if(!authenticate(request.headers.authorization,managerSecret))return json(response,401,{error:{code:"UNAUTHORIZED"}});
    if(request.method==="POST"&&url.pathname==="/v1/self-test/readiness"){const dependency=await codex.readiness(AbortSignal.timeout(2_000));return json(response,dependency.ready?200:503,{ready:dependency.ready,dependency:{codex:dependency}});}
    if(request.method==="POST"&&url.pathname==="/v1/vision/jobs"){
      const input=validate(await readJson(request,config.requestMaxBytes),config);const existing=store.byRequest(input.requestId);
      if(existing)return existing.chatId===input.chatId?json(response,200,publicJob(existing)):json(response,404,{error:{code:"VISION_JOB_NOT_FOUND"}});
      if(store.countPending()>=config.queueMaxPending)return json(response,429,{error:{code:"VISION_QUEUE_FULL",retryAfterMs:5000}});
      if(store.countRoomPending(input.chatId)>=config.queueMaxPendingPerRoom)return json(response,429,{error:{code:"ROOM_QUEUE_LIMIT",retryAfterMs:5000}});
      const job=store.create(input);processor.kick();return json(response,202,publicJob(job));
    }
    const match=url.pathname.match(/^\/v1\/vision\/jobs\/([0-9a-f-]+)$/);if(match){const job=scoped(store,match[1]!,url);if(!job)return json(response,404,{error:{code:"VISION_JOB_NOT_FOUND"}});if(request.method==="GET")return json(response,200,publicJob(job));if(request.method==="DELETE"){const cancelled=processor.cancel(job.id);return json(response,cancelled?202:409,publicJob(store.get(job.id)!));}}
    return json(response,404,{error:{code:"NOT_FOUND"}});
  }catch(error){const code=(error as Error).message.slice(0,64);const status=code==="BODY_TOO_LARGE"?413:code.includes("QUEUE_FULL")?429:400;return json(response,status,{error:{code}});}});
  let closing:Promise<void>|undefined;const shutdown=()=>closing??=(processor.close().then(()=>store.close()));server.on("close",()=>{void shutdown();});return{server,store,processor,shutdown};
}
