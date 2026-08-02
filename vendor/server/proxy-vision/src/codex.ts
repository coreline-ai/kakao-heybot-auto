import { readFileSync } from "node:fs";
import type { VisionConfig } from "./config.js";
import type { VisionResult, VisionTask } from "./types.js";

function validateResult(value:unknown,task:VisionTask):VisionResult{
  if(!value||typeof value!=="object"||Array.isArray(value))throw new Error("VISION_OUTPUT_INVALID");
  const body=value as Record<string,unknown>;
  if(Object.keys(body).some(key=>!["version","task","answer","visibleObjects","extractedText","uncertainty"].includes(key))||
    body.version!==2||body.task!==task||typeof body.answer!=="string"||body.answer.trim().length<1||body.answer.length>480||
    !Array.isArray(body.visibleObjects)||body.visibleObjects.length>20||!Array.isArray(body.extractedText)||body.extractedText.length>20||
    !["low","medium","high"].includes(String(body.uncertainty)))throw new Error("VISION_OUTPUT_INVALID");
  if(body.visibleObjects.some(item=>typeof item!=="string")||body.extractedText.some(item=>typeof item!=="string"))throw new Error("VISION_OUTPUT_INVALID");
  const visibleObjects=body.visibleObjects.map(item=>String(item).trim());
  const extractedText=body.extractedText.map(item=>String(item).trim());
  if(visibleObjects.some(item=>!item||item.length>80)||extractedText.some(item=>!item||item.length>120))throw new Error("VISION_OUTPUT_INVALID");
  return{version:2,task,answer:body.answer.trim(),visibleObjects,extractedText,uncertainty:body.uncertainty as VisionResult["uncertainty"]};
}

function readSecret(path: string): string {
  const value = readFileSync(path, "utf8").trim();
  if (value.length < 32 || value.length > 512) throw new Error("SECRET_INVALID");
  return value;
}

export class VisionCodexClient {
  readonly #secret: string;
  constructor(private readonly config: VisionConfig) { this.#secret = readSecret(config.codexSecretFile); }

  async readiness(signal?: AbortSignal): Promise<{ready:boolean; reason?:string}> {
    try {
      const response=await fetch(`${this.config.codexBaseUrl}/ready`,{signal});
      const body=await response.json() as {ready?:boolean; reason?:string};
      return {ready:response.ok&&body.ready===true, reason:body.reason};
    } catch { return {ready:false,reason:"CODEX_UNAVAILABLE"}; }
  }

  async analyze(requestId:string,image:Buffer,mediaType:string,task:VisionTask,signal:AbortSignal):Promise<VisionResult>{
    const response=await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/vision/analyze`,{
      method:"POST",signal,
      headers:{authorization:`Bearer ${this.#secret}`,"x-heybot-service-id":this.config.codexServiceId,"x-request-id":requestId,"x-heybot-vision-task":task,"content-type":mediaType},
      body:new Uint8Array(image),
    });
    const body=await response.json() as {result?:unknown;error?:{code?:string}};
    if(!response.ok||!body.result) throw new Error(body.error?.code||"CODEX_VISION_FAILED");
    return validateResult(body.result,task);
  }
}
