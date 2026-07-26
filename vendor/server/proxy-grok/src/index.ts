import { mkdirSync } from 'node:fs';
import { loadGrokProxyConfig } from './config/config.js';
import { createGrokServer } from './http/server.js';
const config=loadGrokProxyConfig();mkdirSync(config.runtimeDir,{recursive:true,mode:0o700});const {server,shutdown}=createGrokServer(config);server.listen(config.port,config.host,()=>console.log(JSON.stringify({level:'info',event:'server_started',service:'proxy-grok',host:config.host,port:config.port})));function stop():void{server.close(()=>void shutdown().then(()=>process.exit(0)));setTimeout(()=>process.exit(1),10000).unref();}process.on('SIGTERM',stop);process.on('SIGINT',stop);
