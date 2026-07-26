import { createReadStream, statSync } from "node:fs";
import { createServer, type IncomingMessage, type Server, type ServerResponse } from "node:http";
import { URL } from "node:url";
import { authenticateBearer, readSecret } from "../auth/auth.js";
import type { DrawProxyConfig } from "../config/config.js";
import { CodexClient } from "../clients/codex/client.js";
import { BrushClient } from "../clients/brush/client.js";
import { DrawJobStore } from "../storage/store.js";
import { DrawJobProcessor } from "../orchestration/processor.js";
import { toPublicDrawJob, type CreateDrawJob } from "../jobs/types.js";

const DECIMAL_ID=/^[1-9]\d{0,19}$/;
function json(response:ServerResponse,status:number,body:unknown):void{const data=Buffer.from(JSON.stringify(body));response.writeHead(status,{"content-type":"application/json; charset=utf-8","content-length":data.length,"cache-control":"no-store","x-content-type-options":"nosniff"});response.end(data);}
async function readJson(request:IncomingMessage,limit:number):Promise<unknown>{const chunks:Buffer[]=[];let bytes=0;for await(const chunk of request){const buffer=Buffer.from(chunk);bytes+=buffer.length;if(bytes>limit)throw new Error("BODY_TOO_LARGE");chunks.push(buffer);}try{return JSON.parse(Buffer.concat(chunks).toString("utf8"));}catch{throw new Error("INVALID_JSON");}}
function validateCreate(value:unknown,config:DrawProxyConfig):CreateDrawJob{if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("INVALID_REQUEST");const body=value as Record<string,unknown>;if(Object.keys(body).some(key=>!["requestId","chatId","userId","logId","prompt"].includes(key)))throw new Error("UNSUPPORTED_FIELD");if(typeof body.requestId!=="string"||!/^[A-Za-z0-9._:-]{1,128}$/.test(body.requestId)||typeof body.chatId!=="string"||!DECIMAL_ID.test(body.chatId)||typeof body.userId!=="string"||!DECIMAL_ID.test(body.userId)||typeof body.logId!=="string"||!DECIMAL_ID.test(body.logId)||typeof body.prompt!=="string"||!body.prompt.trim()||body.prompt.length>config.promptMaxChars)throw new Error("INVALID_REQUEST");return {requestId:body.requestId,chatId:body.chatId,userId:body.userId,logId:body.logId,prompt:body.prompt.trim()};}
function scoped(store:DrawJobStore,jobId:string,url:URL){const chatId=url.searchParams.get("chatId");if(!chatId||!DECIMAL_ID.test(chatId))throw new Error("INVALID_CHAT_ID");const job=store.get(jobId);return job?.chatId===chatId?job:undefined;}

export interface DrawServerContext { server:Server; store:DrawJobStore; processor:DrawJobProcessor; shutdown():Promise<void>; }
export function createDrawServer(config:DrawProxyConfig):DrawServerContext{
 const managerSecret=readSecret(config.managerSecretFile);const store=new DrawJobStore(config.databaseFile);const codex=new CodexClient(config);const brush=new BrushClient(config);const processor=new DrawJobProcessor(store,codex,brush,config);processor.start();
 const server=createServer(async(request,response)=>{const url=new URL(request.url??"/","http://127.0.0.1");try{
  if(request.method==="GET"&&url.pathname==="/health")return json(response,200,{ok:true,service:"proxy-draw"});
  if(request.method==="GET"&&url.pathname==="/ready"){const [c,b]=await Promise.all([codex.readiness(AbortSignal.timeout(2_000)),brush.readiness(AbortSignal.timeout(2_000))]);const ready=c.ready&&b.ready;return json(response,ready?200:503,{ready,dependency:{codex:c,brush:b},queue:processor.snapshot()});}
  if(!authenticateBearer(request.headers.authorization,managerSecret))return json(response,401,{error:{code:"UNAUTHORIZED"}});
  if(request.method==="POST"&&url.pathname==="/v1/self-test/readiness"){const [c,b]=await Promise.all([codex.readiness(AbortSignal.timeout(2_000)),brush.readiness(AbortSignal.timeout(2_000))]);const ready=c.ready&&b.ready;return json(response,ready?200:503,{ready,dependency:{codex:c,brush:b}});}
  if(request.method==="POST"&&url.pathname==="/v1/draw/jobs"){const input=validateCreate(await readJson(request,config.requestMaxBytes),config);const existing=store.findByRequest(input.requestId);if(existing){if(existing.chatId!==input.chatId)return json(response,404,{error:{code:"DRAW_JOB_NOT_FOUND"}});return json(response,200,toPublicDrawJob(existing));}if(store.countPending()>=config.queueMaxPending)return json(response,429,{error:{code:"DRAW_QUEUE_FULL",retryAfterMs:5000}});if(store.countRoomPending(input.chatId)>=config.queueMaxPendingPerRoom)return json(response,429,{error:{code:"ROOM_QUEUE_LIMIT",retryAfterMs:5000}});const job=store.create(input);processor.kick();return json(response,202,toPublicDrawJob(job));}
  const file=url.pathname.match(/^\/v1\/draw\/jobs\/([0-9a-f-]+)\/file$/);if(request.method==="GET"&&file){const job=scoped(store,file[1]!,url);if(job?.status!=="succeeded"||!job.artifactPath)return json(response,404,{error:{code:"DRAW_FILE_NOT_FOUND"}});const size=statSync(job.artifactPath).size;response.writeHead(200,{"content-type":"video/mp4","content-length":size,"cache-control":"no-store","x-content-type-options":"nosniff"});createReadStream(job.artifactPath).pipe(response);return;}
  const route=url.pathname.match(/^\/v1\/draw\/jobs\/([0-9a-f-]+)$/);if(route){const job=scoped(store,route[1]!,url);if(!job)return json(response,404,{error:{code:"DRAW_JOB_NOT_FOUND"}});if(request.method==="GET")return json(response,200,toPublicDrawJob(job));if(request.method==="DELETE"){const cancelled=await processor.cancel(job.id);return json(response,cancelled?202:409,toPublicDrawJob(store.get(job.id)!));}}
  return json(response,404,{error:{code:"NOT_FOUND"}});
 }catch(error){const code=(error as Error).message.slice(0,64);return json(response,code==="BODY_TOO_LARGE"?413:400,{error:{code}});}});
 let closing:Promise<void>|undefined;const shutdown=():Promise<void>=>{if(!closing)closing=processor.close().then(()=>store.close());return closing;};server.on("close",()=>{void shutdown();});return {server,store,processor,shutdown};
}
