import { mkdirSync, readFileSync } from "node:fs";
import { loadYoutubeProxyConfig } from "./config.js";
import { YoutubeProcessor } from "./processor.js";
import { createYoutubeRunner } from "./runner.js";
import { createYoutubeServer } from "./server.js";
import { YoutubeJobStore } from "./store.js";
const config=loadYoutubeProxyConfig();const secret=readFileSync(config.managerSecretFile,"utf8").trim();if(!secret)throw new Error("YOUTUBE_MANAGER_SECRET_EMPTY");mkdirSync(config.runtimeDir,{recursive:true,mode:0o700});const processor=new YoutubeProcessor(new YoutubeJobStore(config.databaseFile),createYoutubeRunner(config),config);processor.start();const server=createYoutubeServer(config,processor,secret);server.listen(config.port,config.host);const stop=()=>server.close(()=>void processor.close().then(()=>process.exit(0)));process.on("SIGTERM",stop);process.on("SIGINT",stop);
