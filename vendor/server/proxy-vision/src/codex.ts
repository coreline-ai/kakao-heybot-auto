import { readFileSync } from "node:fs";
import type { VisionConfig } from "./config.js";
import type { VisionResult } from "./types.js";

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

  async analyze(requestId:string,image:Buffer,mediaType:string,signal:AbortSignal):Promise<VisionResult>{
    const response=await fetch(`${this.config.codexBaseUrl}/internal/v1/codex/vision/analyze`,{
      method:"POST",signal,
      headers:{authorization:`Bearer ${this.#secret}`,"x-heybot-service-id":this.config.codexServiceId,"x-request-id":requestId,"content-type":mediaType},
      body:new Uint8Array(image),
    });
    const body=await response.json() as {result?:VisionResult;error?:{code?:string}};
    if(!response.ok||!body.result) throw new Error(body.error?.code||"CODEX_VISION_FAILED");
    return body.result;
  }
}
