import { mkdirSync } from "node:fs";
import { loadAudioProxyConfig } from "./config.js";
import { AudioJobStore } from "./store.js";
import { DefaultAudioPipeline } from "./pipeline.js";
import { AudioJobProcessor } from "./processor.js";
import { createAudioServer } from "./server.js";

const config = loadAudioProxyConfig();
mkdirSync(config.runtimeDir, { recursive: true, mode: 0o700 });
const store = new AudioJobStore(config.databaseFile, config.transcriptKeyFile);
const processor = new AudioJobProcessor(store, new DefaultAudioPipeline(config), config);
store.cleanup(Date.now() - config.transcriptTtlMs);
const cleanup = setInterval(() => store.cleanup(Date.now() - config.transcriptTtlMs), 60 * 60_000);
cleanup.unref();
const server = createAudioServer(config, processor);
server.listen(config.port, config.host, () => console.log(JSON.stringify({ level: "info", event: "server_started", service: "proxy-audio", host: config.host, port: config.port, runner: config.runnerMode })));
function stop(): void { clearInterval(cleanup); server.close(() => void processor.close().then(() => process.exit(0))); setTimeout(() => process.exit(1), 10_000).unref(); }
process.on("SIGTERM", stop); process.on("SIGINT", stop);
