import { mkdirSync } from "node:fs";
import { loadVisionConfig } from "./config.js";
import { createVisionServer } from "./server.js";

const config=loadVisionConfig();mkdirSync(config.runtimeDir,{recursive:true,mode:0o700});const context=createVisionServer(config);
context.server.listen(config.port,config.host,()=>console.log(JSON.stringify({level:"info",event:"server_started",service:"proxy-vision",host:config.host,port:config.port})));
function shutdown():void{context.server.close(()=>void context.shutdown().then(()=>process.exit(0)));setTimeout(()=>process.exit(1),10_000).unref();}
process.on("SIGTERM",shutdown);process.on("SIGINT",shutdown);
