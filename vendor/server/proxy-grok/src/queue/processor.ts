import { unlinkSync } from 'node:fs';
import type { GrokProxyConfig } from '../config/config.js';
import { GrokCliRunner } from '../cli/runner.js';
import { GrokJobStore } from '../storage/store.js';

function waitForRetry(milliseconds: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(resolve, milliseconds);
    signal.addEventListener('abort', () => {
      clearTimeout(timer);
      reject(new Error('JOB_CANCELLED'));
    }, { once: true });
  });
}

export class GrokJobProcessor {
  #active=false; #closed=false; #controller?:AbortController; #activeJobId?:string;
  constructor(private readonly store:GrokJobStore, private readonly runner:GrokCliRunner, private readonly config:GrokProxyConfig) {}
  start():void { this.kick(); }
  snapshot():{active:number;pending:number;maxConcurrency:number}{return {active:this.#active?1:0,pending:this.store.countPending(),maxConcurrency:1};}
  kick():void { if(this.#active||this.#closed)return; const job=this.store.next(); if(!job)return; if(!this.store.running(job.id))return this.kick(); this.#active=true; this.#activeJobId=job.id; const controller=new AbortController(); this.#controller=controller; const timeout=setTimeout(()=>controller.abort(),this.config.jobTimeoutMs); void this.runWithSingleZdrRetry(job.id, job.prompt, controller.signal).then((a)=>this.store.succeed(job.id,a)).catch((e:Error)=>{if(this.store.get(job.id)?.status!=='cancelled')this.store.fail(job.id,e.message);}).finally(()=>{clearTimeout(timeout);this.#active=false;this.#activeJobId=undefined;this.#controller=undefined;setImmediate(()=>this.kick());}); }
  async cancel(id:string):Promise<boolean>{const changed=this.store.cancel(id); if(this.#activeJobId===id){this.runner.cancel(id);this.#controller?.abort();} return changed;}
  async close():Promise<void>{this.#closed=true;this.#controller?.abort();}

  private async runWithSingleZdrRetry(jobId: string, prompt: string, signal: AbortSignal) {
    try {
      return await this.runner.run(jobId, prompt, signal);
    } catch (error) {
      if (!(error instanceof Error) || error.message !== 'VIDEO_ZDR_UPLOAD_URL_REQUIRED') throw error;
      await waitForRetry(60_000, signal);
      return this.runner.run(jobId, prompt, signal);
    }
  }
}
