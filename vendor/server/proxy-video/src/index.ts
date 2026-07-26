import { mkdirSync } from "node:fs";
import { loadVideoProxyConfig } from "./config/config.js";
import { createVideoServer } from "./http/server.js";

const config = loadVideoProxyConfig();
mkdirSync(config.runtimeDir, { recursive: true, mode: 0o700 });
const { server, shutdown: shutdownJobs } = createVideoServer(config);

server.listen(config.port, config.host, () => {
  console.log(
    JSON.stringify({
      level: "info",
      event: "server_started",
      service: "proxy-video",
      host: config.host,
      port: config.port,
      queueConcurrency: config.queueConcurrency,
    }),
  );
});

function shutdown(): void {
  server.close(() => {
    void shutdownJobs().then(() => process.exit(0));
  });
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
