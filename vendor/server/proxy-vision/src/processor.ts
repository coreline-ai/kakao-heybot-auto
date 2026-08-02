import type { VisionConfig } from "./config.js";
import { VisionCodexClient } from "./codex.js";
import { fetchSource } from "./source.js";
import { VisionStore } from "./store.js";

export class VisionProcessor {
  #active=0; #closed=false; readonly #controllers=new Map<string,AbortController>(); readonly #tasks=new Set<Promise<void>>();
  constructor(readonly store:VisionStore,private readonly codex:VisionCodexClient,private readonly config:VisionConfig){}
  start():void{this.kick();}
  snapshot():Record<string,number>{return{active:this.#active,pending:this.store.countPending(),maxConcurrency:this.config.queueConcurrency};}
  async close():Promise<void>{this.#closed=true;for(const controller of this.#controllers.values())controller.abort();await Promise.allSettled([...this.#tasks]);}
  cancel(id:string):boolean{const changed=this.store.cancel(id);this.#controllers.get(id)?.abort();return changed;}
  kick():void{
    if(this.#closed)return;
    while(this.#active<this.config.queueConcurrency){
      const job=this.store.next();if(!job)return;
      if(Date.now()-job.createdAt>this.config.queueWaitTimeoutMs){this.store.fail(job.id,"VISION_QUEUE_WAIT_TIMEOUT");continue;}
      if(!this.store.markRunning(job.id))continue;
      this.#active+=1;const controller=new AbortController();this.#controllers.set(job.id,controller);
      const task=this.run(job.id,controller.signal).catch((error:Error)=>{
        if(this.store.get(job.id)?.status!=="cancelled")this.store.fail(job.id,error.message.replace(/[^A-Z0-9_]/g,"_").slice(0,64));
      }).finally(()=>{this.#active-=1;this.#controllers.delete(job.id);setImmediate(()=>this.kick());});
      this.#tasks.add(task);void task.finally(()=>this.#tasks.delete(task));
    }
  }
  private async run(id:string,signal:AbortSignal):Promise<void>{
    const job=this.store.get(id);if(!job||!job.source.url)throw new Error("SOURCE_UNAVAILABLE");
    const source=await fetchSource(job.source,this.config,AbortSignal.any([signal,AbortSignal.timeout(this.config.fetchTimeoutMs)]));
    const result=await this.codex.analyze(job.requestId,source.data,source.mediaType,job.task,AbortSignal.any([signal,AbortSignal.timeout(this.config.codexTimeoutMs)]));
    this.store.succeed(id,result);
  }
}
