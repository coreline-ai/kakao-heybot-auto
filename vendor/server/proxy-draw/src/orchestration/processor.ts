import { chmodSync, mkdirSync, renameSync, unlinkSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import type { DrawProxyConfig } from "../config/config.js";
import { CodexClient } from "../clients/codex/client.js";
import { BrushClient } from "../clients/brush/client.js";
import { validatePng } from "../images-qc.js";
import { validateMp4 } from "../videos/qc.js";
import { DrawJobStore } from "../storage/store.js";

function delay(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolvePromise, reject) => { const timer=setTimeout(resolvePromise,milliseconds); signal.addEventListener("abort",()=>{clearTimeout(timer);reject(new Error("JOB_CANCELLED"));},{once:true}); });
}

export function sourceInstruction(prompt: string): string {
  const subject = prompt.replace(/[\r\n\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim().slice(0, 300);
  return [
    "Create exactly one polished, high-detail vertical 9:16 PNG illustration for a precision pen-outline then brush-color reveal video.",
    "Use a clean white artist-paper background and a single fully visible subject with a deliberate, well-balanced composition.",
    "The artwork must have refined charcoal linework with varied line weight, rich opaque colour, controlled highlights and shadows, tactile hand-painted pigment texture, and clearly defined material details. It must look like a finished illustration, not flat vector art or a colouring-book page.",
    "Keep colours and edges sufficiently separated for an outline-then-paint animation, while preserving nuanced shading and detail. Make the requested action and facial expression immediately readable.",
    "No text, letters, logo, watermark, grid, photorealism, 3D render, dark shadow, collage, or multiple panels.",
    `Creative subject in Korean: ${JSON.stringify(subject)}`,
  ].join(" ");
}

export class DrawJobProcessor {
  readonly #controllers = new Map<string, AbortController>(); readonly #tasks = new Set<Promise<void>>();
  #active=0; #closed=false; #cleanupTimer?: NodeJS.Timeout; #closePromise?: Promise<void>;
  constructor(readonly store: DrawJobStore, private readonly codex: CodexClient, private readonly brush: BrushClient, private readonly config: DrawProxyConfig) {}
  start(): void { this.#cleanupTimer=setInterval(()=>this.cleanup(),60_000); this.#cleanupTimer.unref(); this.kick(); }
  close(): Promise<void> { if(this.#closePromise)return this.#closePromise; this.#closed=true; if(this.#cleanupTimer)clearInterval(this.#cleanupTimer); for(const controller of this.#controllers.values())controller.abort(); this.#closePromise=Promise.allSettled([...this.#tasks]).then(()=>undefined); return this.#closePromise; }
  snapshot(): {active:number;maxConcurrency:number;pending:number} { return {active:this.#active,maxConcurrency:this.config.queueConcurrency,pending:this.store.countPending()}; }
  kick(): void { if(this.#closed)return; while(this.#active<this.config.queueConcurrency){const job=this.store.nextQueued();if(!job)return;if(Date.now()-job.createdAt>this.config.queueWaitTimeoutMs){this.store.fail(job.id,"DRAW_QUEUE_WAIT_TIMEOUT");continue;}if(!this.store.markRunning(job.id))continue;this.#active+=1;const controller=new AbortController();this.#controllers.set(job.id,controller);const task=this.run(job.id,controller.signal).catch((error:Error)=>{if(this.store.get(job.id)?.status!=="cancelled")this.store.fail(job.id,String(error.message||error).replace(/[^A-Z0-9_]/g,"_").slice(0,64));}).finally(()=>{this.#controllers.delete(job.id);this.#active-=1;setImmediate(()=>this.kick());});this.#tasks.add(task);void task.finally(()=>this.#tasks.delete(task));} }
  async cancel(jobId:string):Promise<boolean>{const job=this.store.get(jobId);if(!job)return false;const changed=this.store.cancel(jobId);this.#controllers.get(jobId)?.abort();if(job.codexJobId)await this.codex.cancel(job.codexJobId);if(job.brushJobId)await this.brush.cancel(job.brushJobId);return changed;}
  private async run(jobId:string,signal:AbortSignal):Promise<void>{
    const job=this.store.get(jobId);if(!job)throw new Error("JOB_NOT_FOUND");
    const created=await this.codex.create(`draw-source:${job.id}`,sourceInstruction(job.prompt),signal);this.store.setCodexJob(job.id,created.jobId);
    const codex=await this.awaitCodex(created,signal);const artifact=codex.artifacts[0];if(!artifact||codex.artifacts.length!==1||artifact.mediaType!=="image/png")throw new Error("CODEX_ARTIFACT_CONTRACT");
    const source=await this.codex.download(codex.jobId,artifact.artifactId,signal);const sourceQc=validatePng(source,this.config.imageMaxBytes);if(artifact.sha256!==sourceQc.sha256)throw new Error("CODEX_ARTIFACT_HASH_MISMATCH");
    const seed=this.seed(job.id);const brush=await this.brush.create(`draw-render:${job.id}`,source,seed,signal);this.store.setBrushJob(job.id,brush.jobId);
    const rendered=await this.awaitBrush(brush,signal);if(!rendered.file||rendered.file.mediaType!=="video/mp4")throw new Error("BRUSH_ARTIFACT_CONTRACT");
    const data=await this.brush.download(rendered.jobId,signal);const finalPath=resolve(this.config.runtimeDir,"artifacts",`${job.id}.mp4`);const temp=`${finalPath}.tmp`;mkdirSync(dirname(finalPath),{recursive:true,mode:0o700});writeFileSync(temp,data,{mode:0o600});const qc=await validateMp4(temp,data,this.config.videoMaxBytes,this.config.ffprobeCommand);if(qc.width!==1080||qc.height!==1920||qc.durationSeconds<9.9||qc.durationSeconds>10.1)throw new Error("PEN_BRUSH_VIDEO_QC_FAILED");renameSync(temp,finalPath);chmodSync(finalPath,0o600);this.store.succeed(job.id,{path:finalPath,bytes:qc.bytes,sha256:qc.sha256});
  }
  private async awaitCodex(initial:Awaited<ReturnType<CodexClient["create"]>>,signal:AbortSignal){let state=initial;const deadline=Date.now()+this.config.codexJobTimeoutMs;while(state.status==="queued"||state.status==="running"){if(Date.now()>=deadline){await this.codex.cancel(state.jobId);throw new Error("CODEX_JOB_TIMEOUT");}await delay(500,signal);state=await this.codex.get(state.jobId,signal);}if(state.status!=="succeeded")throw new Error(state.error?.code||`CODEX_${state.status.toUpperCase()}`);return state;}
  private async awaitBrush(initial:Awaited<ReturnType<BrushClient["create"]>>,signal:AbortSignal){let state=initial;const deadline=Date.now()+this.config.brushJobTimeoutMs;while(state.status==="queued"||state.status==="running"){if(Date.now()>=deadline){await this.brush.cancel(state.jobId);throw new Error("BRUSH_JOB_TIMEOUT");}await delay(1_000,signal);state=await this.brush.get(state.jobId,signal);}if(state.status!=="succeeded")throw new Error(state.error?.code||`BRUSH_${state.status.toUpperCase()}`);return state;}
  private seed(id:string):number{let value=0;for(const byte of Buffer.from(id.replaceAll("-",""),"hex"))value=((value*33)+byte)%2_147_483_647;return Math.max(1,value);}
  private cleanup():void{const cutoff=Date.now()-this.config.artifactRetentionHours*60*60*1000;for(const job of this.store.expiredArtifacts(cutoff)){if(!job.artifactPath)continue;try{unlinkSync(job.artifactPath);}catch{}}}
}
