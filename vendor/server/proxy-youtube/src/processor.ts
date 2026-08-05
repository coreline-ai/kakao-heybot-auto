import { unlink } from "node:fs/promises";
import type { YoutubeProxyConfig } from "./config.js";
import type { YoutubeRunner } from "./runner.js";
import { YoutubeJobStore } from "./store.js";
import type { CreateYoutubeJob } from "./types.js";
import { publicJob } from "./types.js";
export class YoutubeProcessor { #active=new Map<string,AbortController>();#closed=false;#timer?:NodeJS.Timeout;
 constructor(private store:YoutubeJobStore,private runner:YoutubeRunner,private config:YoutubeProxyConfig){}
 start(){this.#timer=setInterval(()=>void this.cleanup(),60_000);this.#timer.unref();this.kick()} async close(){this.#closed=true;if(this.#timer)clearInterval(this.#timer);for(const c of this.#active.values())c.abort();while(this.#active.size)await new Promise(r=>setTimeout(r,10));this.store.close()}
 readiness(){return this.runner.readiness()} create(input:CreateYoutubeJob){const old=this.store.findRequest(input.requestId);if(!old&&this.store.pending(input.chatId)>=this.config.maxPendingPerRoom)throw new Error("ROOM_QUEUE_LIMIT");const r=this.store.createOrGet(input);this.kick();return {job:publicJob(r.job),created:r.created}} get(id:string,chat:string){const j=this.store.get(id);return j?.chatId===chat?publicJob(j):undefined} file(id:string,chat:string){const j=this.store.get(id);return j?.chatId===chat&&j.status==="succeeded"&&j.artifactPath?j:undefined} cancel(id:string,chat:string){const j=this.store.get(id);if(!j||j.chatId!==chat)return;this.#active.get(id)?.abort();this.store.cancel(id);return publicJob(this.store.get(id)!)}
 kick():void{if(this.#closed||this.#active.size>=this.config.maxConcurrency)return;const next=this.store.next();if(!next)return;const job=this.store.claim(next.id);if(!job)return this.kick();const c=new AbortController();this.#active.set(job.id,c);void this.runner.download(job.id,job.url,c.signal).then(a=>{if(c.signal.aborted)this.store.cancel(job.id);else this.store.succeed(job.id,a)}).catch(e=>{if(c.signal.aborted)this.store.cancel(job.id);else this.store.fail(job.id,(e as Error).message)}).finally(()=>{this.#active.delete(job.id);this.kick()});this.kick()}
 async cleanup(){for(const job of this.store.expired(Date.now()-this.config.artifactTtlMs)){if(job.artifactPath)await unlink(job.artifactPath).catch(()=>{})}}
}
