import { mkdirSync } from "node:fs";
import { loadDrawProxyConfig } from "./config/config.js";
import { createDrawServer } from "./http/server.js";
const config=loadDrawProxyConfig();mkdirSync(config.runtimeDir,{recursive:true,mode:0o700});const {server,shutdown:shutdownJobs}=createDrawServer(config);server.listen(config.port,config.host,()=>console.log(JSON.stringify({level:"info",event:"server_started",service:"proxy-draw",host:config.host,port:config.port})));function stop():void{server.close(()=>void shutdownJobs().then(()=>process.exit(0)));setTimeout(()=>process.exit(1),10_000).unref();}process.on("SIGTERM",stop);process.on("SIGINT",stop);
