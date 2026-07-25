import { mkdirSync } from "node:fs";
import { loadImageProxyConfig } from "./config/config.js";
import { createImageServer } from "./http/server.js";

const config = loadImageProxyConfig();
mkdirSync(config.runtimeDir, { recursive: true, mode: 0o700 });
const { server, shutdown: shutdownJobs } = createImageServer(config);

server.listen(config.port, config.host, () => {
  console.log(
    JSON.stringify({
      level: "info",
      event: "server_started",
      service: "proxy-image",
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
