import { loadManagerConfig } from "./config/config.js";
import { createManagerServer } from "./http/server.js";

const config = loadManagerConfig();
const { server } = createManagerServer(config);

server.listen(config.port, config.host, () => {
  console.log(
    JSON.stringify({
      level: "info",
      event: "server_started",
      service: "proxy-manager",
      host: config.host,
      port: config.port,
      lifecycleEnabled: config.lifecycleEnabled,
    }),
  );
});

function shutdown(): void {
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
